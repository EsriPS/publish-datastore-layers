# publish-datastore-layers

Publish every feature class and table from a registered relational (enterprise geodatabase) data
store on ArcGIS Enterprise as a map image layer with an associated feature
layer — quickly, and without copying any data.

It fulfills the same goal as the built-in bulk "[publish all layers](https://developers.arcgis.com/rest/users-groups-and-items/publish-layers/)" operation. This tool publishes each layer by
reference: the published service points at the geodatabase that the server
already knows about, so nothing is copied. A full run of ~90 layers containing a total of hundreds of millions of features takes about **75 minutes**.

> The main tool is [`publish_arcpy.py`](publish_arcpy.py). It runs with the Python
> that comes built into ArcGIS Pro — you do not need to install Python
> yourself.

---

## Is this the right tool for me?

Use this if **all** of the following are true:

- You have an **ArcGIS Enterprise** portal with a **registered relational
  (enterprise geodatabase) data store**.
- You have **ArcGIS Pro** (a version in the same family as
  your Enterprise deployment).
- You want each dataset published as a **map image layer + feature layer**.
- Your portal account has **Publisher** or **Administrator** privileges.
- You cannot use the built-in bulk "publish all layers" (`/allDatasets/publishLayers` of the Sharing API) operation.
---

## Prerequisites

A quick checklist before you start:

- [ ] **ArcGIS Pro** installed on a machine that can connect
  to the enterprise geodatabase configured as a user-managed relational data store in your portal.
- [ ] A **portal account** with Publisher/Administrator privileges.
- [ ] You can **sign in to the portal inside ArcGIS Pro** (the tool reuses that
      sign-in, so single sign-on is handled by Pro for you).
- [ ] An **`.sde` connection file** to the same publisher connection the data store points at
      (see [Step 2](#step-2--create-the-sde-connection-file)).

---

## Step 1 — Get the code

**Option A — Download a ZIP (easiest):**

1. Open the repository on GitHub:
   `https://github.com/EsriPS/publish-datastore-layers`
2. Click the green **Code** button → **Download ZIP**.
3. Extract it, e.g. to `C:\projects\publish-datastore-layers`.

**Option B — Clone with git:**

```powershell
git clone https://github.com/EsriPS/publish-datastore-layers.git
```

---

## Step 2 — Create the `.sde` connection file

The tool needs a database connection file (`.sde`) that points at the **same
publisher geodatabase** as the registered data store. If the connection does not match the
registered store, the layer would be **copied** instead of referenced (slow) —
the tool detects this and skips it (see [Troubleshooting](#troubleshooting)).

**Easiest — using ArcGIS Pro's Catalog:**

1. In ArcGIS Pro, open the **Catalog** pane.
2. Right-click **Databases** → **New Database Connection**.
3. Fill in the database platform, server/instance, authentication, and database.
4. A `.sde` file is created; copy it into your project folder
   (e.g. `C:\publish-datastore-layers`).

**Alternative — one line of Python** (run in the Pro Python window):

```python
import arcpy
arcpy.management.CreateDatabaseConnection(
    r"C:\projects\publish-datastore-layers", "egdb.sde",
    database_platform="POSTGRESQL",
    instance="your-db-host", database="egdb",
    account_authentication="DATABASE_AUTH",
    username="dbowner", password="********",
)
```

> The `.sde` file (and its credentials) is **git-ignored** and never committed.

---

## Step 3 — Sign in through ArcGIS Pro

1. Open **ArcGIS Pro**.
2. Sign in to your portal (top-right) — e.g.
   `https://portal.example.com/arcgis`.

The tool publishes to whatever portal Pro is signed into. If the `--portal` you
pass does not match, the tool stops with a clear message and does **not** publish
to the wrong place.

---

## Step 4 — Configure

There is nothing to edit in a config file — you pass a few options on the command
line. The most important ones (with their built-in defaults, all overridable):

| Option | Default | What it is |
| --- | --- | --- |
| `--portal` | the portal ArcGIS Pro is signed into | Your portal URL (only needed to pin/verify the sign-in) |
| `--folder` | root (no folder) | The folder (in the portal **and** on the server) to publish into |
| `--sde` | `egdb.sde` | Your database connection file |
| `--capabilities` | `Query` | Feature layer access; `Query` = read-only |

---

## Step 5 — Test one layer first

Before publishing everything, prove it works on a single layer.

**Stage one layer** (prepares it, but does not publish yet):

```powershell
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" publish_arcpy.py --phase stage --limit 1
```

Look for `staged` in the output and **no `24011` warning**. Then **publish it**:

```powershell
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" publish_arcpy.py --phase publish
```

Success looks like `published <name>`. Open the portal folder and confirm the new
item has both a **MapServer** and a **FeatureServer** that respond.

---

## Step 6 — Run the full batch

Publish everything. By default services go to the **root** of the portal/server;
add `--folder <name>` to publish into a folder instead:

```powershell
# to the root
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" publish_arcpy.py --phase all

# or into a folder
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" publish_arcpy.py --phase all --folder MyFolder
```

- **Re-runs are safe.** Anything already published in that location is reported as
  `skipped_existing`, so you can stop and restart without creating duplicates.
- **To refresh everything**, add `--overwrite` to republish all layers.

---

## How to run the command

Always launch with **ArcGIS Pro's Python** (`propy.bat`), not a regular `python`.
The canonical form, run from the project folder in **PowerShell**:

```powershell
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" publish_arcpy.py --phase all
```

---

## Command reference

| Option | Default | Description |
| --- | --- | --- |
| `--phase` | `all` | `stage` (prepare only), `publish` (upload prepared files), or `all` |
| `--folder` | root (no folder) | Portal **and** server folder to publish into |
| `--portal` | active Pro sign-in | Portal URL; validated against the ArcGIS Pro sign-in |
| `--server` | same as `--portal` | Hosting server URL (equals the portal URL on this deployment) |
| `--sde` | bundled `.sde` | Database connection file |
| `--capabilities` | `Query` | Feature capabilities, e.g. `Query` or `Create,Update,Delete,Sync,Query` |
| `--datasets` | (all) | Only datasets whose name contains this text |
| `--limit` | (all) | Process at most N datasets |
| `--overwrite` | off | Republish/overwrite existing services instead of skipping them |
| `--scratch` | `_publish_scratch` | Working folder for temporary files |

---

## Checking the results

After every run the tool writes **`publish_results.json`** with one entry per
dataset. Possible statuses:

| Status | Meaning |
| --- | --- |
| `published` | Successfully published. |
| `staged` | Prepared, waiting to be published (stage-only runs). |
| `skipped_existing` | A service with this name already exists in the folder. |
| `skipped_would_copy` | The `.sde` did not match the registered store — skipped to avoid a slow data copy. |
| `stage_failed` / `publish_failed` | Something went wrong; the entry includes the error. |

To verify a service, open it in the portal folder and confirm both its
**MapServer** and **FeatureServer** endpoints load.

---

## How it works (optional background)

- **By reference:** services are published with `copyDataToServer = False`, so the
  server references the registered geodatabase instead of copying data.
- **Two phases:** the tool first **stages** each dataset to a service-definition
  file (`.sd`), then **uploads** those files. Splitting the work makes runs
  resumable and keeps the door open to faster, parallel publishing later.
- Staging requires a saved ArcGIS Pro project; the tool creates a fresh blank one
  automatically for each dataset.

---

## Troubleshooting

| Symptom | Cause & fix |
| --- | --- |
| "not signed in" or "portal … but `--portal` is …" | Sign in to the correct portal **inside ArcGIS Pro**, then re-run. |
| `skipped_would_copy` / analyzer `24011` | The `.sde` does not match the registered data store. Recreate it against the same instance/database/user. |
| `ModuleNotFoundError: arcpy` | You ran plain `python`. Use the full `propy.bat` path shown above. |
| Staging error `999999` | An internal staging hiccup; make sure nothing is editing the scratch folder mid-run, then re-run. |
| Staging is unusually slow for a layer | Its stored extent may be stale — run `arcpy.management.RecalculateFeatureClassExtent` on it once. |

---

## Notes & safety

- The `.sde` file and `publish_results.json` are **git-ignored** in this repository; connection
  credentials are never committed.
- Publishing writes to a **shared portal**. Use `--limit` or `--datasets` to scope
  a run while testing.
- Feature layers are **read-only** (`Query`) by default; pass `--capabilities` to
  enable editing.
