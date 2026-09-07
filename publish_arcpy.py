"""Batch-publish every feature class and table from a registered relational
(egdb) data store as a MAP_IMAGE map service with an associated feature layer,
BY REFERENCE (copyDataToServer=False, no data copy).

Two phases, decoupled through .sd files so publishing can later be parallelized
with the ArcGIS Python API without reworking staging:

  stage   : each dataset -> a staged service definition (.sd)   [sequential arcpy]
  publish : each .sd      -> UploadServiceDefinition             [sequential now]

Run with ArcGIS Pro's Python so arcpy and the active portal session are used:

    & "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\Scripts\\propy.bat" publish_arcpy.py --phase all

The portal is whatever ArcGIS Pro is signed into; --portal is validated against
that sign-in (arcpy cannot script the SAML sign-in). On ArcGIS Enterprise on
Kubernetes deployment the hosting server URL always equals the org/portal URL.
"""
import argparse
import json
import os
import re
import shutil
import sys

import arcpy

# ---- Defaults (all overridable via CLI) ----------------------------------
DEFAULT_SDE = "egdb.sde"            # database connection file in the current folder
DEFAULT_PORTAL = None               # default: the portal ArcGIS Pro is signed into
DEFAULT_FOLDER = ""                  # default: publish to the root (no folder)
DEFAULT_SCRATCH = "_publish_scratch"
DEFAULT_CAPABILITIES = "Query"      # read-only feature layer
MAP_OPERATIONS = "Map,Query,Data"
TAGS = "published-by-script"
RESULTS_FILE = "publish_results.json"
# --------------------------------------------------------------------------


def norm_url(url: str) -> str:
    return (url or "").rstrip("/").lower()


def service_name_for(dataset_name: str, used: set) -> str:
    """Service name = dataset base name, owner/schema dropped, sanitized,
    capped at 120 chars, de-duplicated against names already assigned."""
    base = os.path.basename(dataset_name).rsplit(".", 1)[-1]
    name = re.sub(r"[^0-9A-Za-z_]", "_", base).strip("_")
    if name and name[0].isdigit():
        name = "_" + name
    name = name[:120] or "layer"
    candidate = name
    n = 1
    while candidate.lower() in used:
        n += 1
        candidate = f"{name[:116]}_{n}"
    used.add(candidate.lower())
    return candidate


def resolve_portal(requested) -> str:
    """arcpy publishes to whatever Pro is signed into. Use that portal unless the
    caller pinned a specific --portal, in which case it must match."""
    active = arcpy.GetActivePortalURL()
    if not active:
        sys.exit("ERROR: not signed in to any portal. Sign in in ArcGIS Pro, "
                 "then re-run.")
    if requested and norm_url(requested) != norm_url(active):
        sys.exit(f"ERROR: ArcGIS Pro is signed in to {active} but --portal is "
                 f"{requested}. Sign in to {requested} in Pro, then re-run.")
    return active.rstrip("/")


def clean_connection(sde: str, scratch: str) -> str:
    """Copy the .sde to a stable, parenthesis-free path inside scratch."""
    dst = os.path.join(scratch, "egdb.sde")
    shutil.copyfile(sde, dst)
    return dst


def enumerate_datasets(sde: str):
    """Feature classes and tables, including those inside feature datasets."""
    items = []
    for dirpath, _dirnames, names in arcpy.da.Walk(
        sde, datatype=["FeatureClass", "Table"]
    ):
        for n in names:
            items.append(os.path.join(dirpath, n))
    return sorted(items)


def existing_service_names(server_url: str, folder: str) -> set:
    """Base service names already present in the server's target folder."""
    try:
        from arcgis.gis import GIS
        gis = GIS("pro")
        path = f"{server_url.rstrip('/')}/rest/services"
        if folder:
            path += f"/{folder}"
        info = gis._con.get(path, {"f": "json"})
        names = set()
        for svc in (info or {}).get("services", []):
            base = svc.get("name", "").split("/")[-1]
            if base:
                names.add(base.lower())
        return names
    except Exception as exc:  # folder may not exist yet -> nothing published
        print(f"  (could not list existing services in {folder or 'root'}: {exc})")
        return set()


def make_blank_project(scratch: str):
    proj_dir = os.path.join(scratch, "proj")
    if os.path.isdir(proj_dir):
        shutil.rmtree(proj_dir, ignore_errors=True)
    os.makedirs(proj_dir, exist_ok=True)
    return arcpy.mp.CreateArcGISProject(proj_dir, "stage", create_parent_folder=False)


def stage_one(fc_path, name, cfg, sd_dir) -> dict:
    """Build a fresh project, add the layer, and stage a by-reference .sd."""
    result = {"dataset": fc_path, "service_name": name}
    aprx = make_blank_project(cfg["scratch"])
    m = aprx.createMap(name[:60] or "Map", "Map")
    added = m.addDataFromPath(fc_path)
    aprx.save()  # StageService requires the project persisted to disk

    sddraft = m.getWebLayerSharingDraft("FEDERATED_SERVER", "MAP_IMAGE", name, [added])
    sddraft.federatedServerUrl = cfg["server"]
    sddraft.copyDataToServer = False
    if cfg["folder"]:
        sddraft.portalFolder = cfg["folder"]
        sddraft.serverFolder = cfg["folder"]
    sddraft.mapOperations = MAP_OPERATIONS
    sddraft.extension.feature.isEnabled = True
    sddraft.extension.feature.featureCapabilities = cfg["capabilities"]
    sddraft.overwriteExistingService = cfg["overwrite"]
    sddraft.tags = TAGS
    sddraft.summary = f"Published from egdb data store as {name} (by reference)"

    sddraft_path = os.path.join(sd_dir, name + ".sddraft")
    sd_path = os.path.join(sd_dir, name + ".sd")
    sddraft.exportToSDDraft(sddraft_path)

    try:
        arcpy.server.StageService(sddraft_path, sd_path)
    except arcpy.ExecuteError:
        result["status"] = "stage_failed"
        result["error"] = arcpy.GetMessages(2)
        return result

    warnings = arcpy.GetMessages(1)
    if "24011" in warnings:
        result["status"] = "skipped_would_copy"
        result["warnings"] = warnings
        return result

    result["status"] = "staged"
    result["sd"] = sd_path
    return result


def publish_one(sd_path, name, server_url) -> dict:
    result = {"service_name": name, "sd": sd_path}
    try:
        arcpy.server.UploadServiceDefinition(sd_path, server_url)
        result["status"] = "published"
    except arcpy.ExecuteError:
        result["status"] = "publish_failed"
        result["error"] = arcpy.GetMessages(2)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "publish_failed"
        result["error"] = str(exc)
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--phase", choices=["stage", "publish", "all"], default="all")
    p.add_argument("--sde", default=DEFAULT_SDE)
    p.add_argument("--portal", default=DEFAULT_PORTAL,
                   help="portal URL (default: the portal ArcGIS Pro is signed into)")
    p.add_argument("--server", default=None,
                   help="hosting server URL (default: same as --portal)")
    p.add_argument("--folder", default=DEFAULT_FOLDER,
                   help="portal AND server folder (default: root / no folder)")
    p.add_argument("--scratch", default=DEFAULT_SCRATCH)
    p.add_argument("--capabilities", default=DEFAULT_CAPABILITIES,
                   help='feature capabilities, e.g. "Query" or "Create,Update,Delete,Sync,Query"')
    p.add_argument("--datasets", default=None,
                   help="only datasets whose name contains this substring")
    p.add_argument("--limit", type=int, default=None,
                   help="process at most N datasets (after filtering)")
    p.add_argument("--overwrite", action="store_true",
                   help="overwrite existing services instead of skipping them")
    args = p.parse_args()
    args.scratch = os.path.abspath(args.scratch)  # StageService requires full paths

    portal = resolve_portal(args.portal)
    server = args.server or portal
    cfg = {
        "server": server,
        "folder": args.folder,
        "scratch": args.scratch,
        "capabilities": args.capabilities,
        "overwrite": args.overwrite,
    }

    os.makedirs(args.scratch, exist_ok=True)
    sd_dir = os.path.join(args.scratch, "sd")
    os.makedirs(sd_dir, exist_ok=True)
    arcpy.env.overwriteOutput = True

    print(f"portal   : {portal}")
    print(f"server   : {server}")
    print(f"folder   : {args.folder or '(root)'}")
    print(f"phase    : {args.phase}")

    results = []

    if args.phase in ("stage", "all"):
        sde = clean_connection(args.sde, args.scratch)
        datasets = enumerate_datasets(sde)
        if args.datasets:
            datasets = [d for d in datasets if args.datasets.lower() in d.lower()]
        if args.limit is not None:
            datasets = datasets[: args.limit]

        existing = set() if args.overwrite else existing_service_names(server, args.folder)
        used_names = set()
        print(f"{len(datasets)} dataset(s) to stage "
              f"({len(existing)} already in {args.folder or 'root'})")

        for fc in datasets:
            name = service_name_for(fc, used_names)
            if not args.overwrite and name.lower() in existing:
                r = {"dataset": fc, "service_name": name, "status": "skipped_existing"}
            else:
                r = stage_one(fc, name, cfg, sd_dir)
            print(f"  {r['status']:20} {name}")
            results.append(r)

    if args.phase == "publish":
        for fn in sorted(os.listdir(sd_dir)):
            if fn.endswith(".sd"):
                name = fn[:-3]
                results.append({"dataset": None, "service_name": name,
                                "status": "staged", "sd": os.path.join(sd_dir, fn)})

    if args.phase in ("publish", "all"):
        to_publish = [r for r in results if r.get("status") == "staged"]
        print(f"publishing {len(to_publish)} staged service definition(s)")
        for r in to_publish:
            pub = publish_one(r["sd"], r["service_name"], server)
            r["status"] = pub["status"]
            if "error" in pub:
                r["error"] = pub["error"]
            print(f"  {r['status']:20} {r['service_name']}")

    with open(RESULTS_FILE, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("---- summary ----")
    for status, n in sorted(counts.items()):
        print(f"  {status:20} {n}")
    print(f"results written to {RESULTS_FILE}")

    failed = counts.get("stage_failed", 0) + counts.get("publish_failed", 0)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
