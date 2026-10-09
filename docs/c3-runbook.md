# Step C3: live verification runbook

Checkpoint C (SPEC §16.3) is reached when a read-only capture of a controlled Quickbase test app, normalized and
assessed, gives S01's findings. This runbook turns Appendix D of the spec into commands. Only the owner of a
Quickbase account can do it; the capture itself never writes to Quickbase.

**Use a test app only.** Never point the tool at a production app or real data. The data imported here is the
synthetic North Creek project. Keep everything captured under the gitignored `local/` directory.

**Install the Quickbase extra first.** Both the builder and `capture-quickbase` need it:

```bash
uv sync --extra quickbase
```

`uv sync --all-extras`, from the README's quick start, includes it.

## Fast path: build the app with one command

`tools/qb_build_test_app.py` replaces steps 1 to 6. You need a Quickbase account in which you may create apps,
and a user token for your own user.

1. Put the token in the environment, as in step 5:

   ```powershell
   $env:QB_USER_TOKEN = Read-Host -MaskInput "Quickbase user token"
   ```

2. Preview the plan. Nothing is sent:

   ```bash
   uv run python tools/qb_build_test_app.py --realm YOURREALM.quickbase.com
   ```

3. Build:

   ```bash
   uv run python tools/qb_build_test_app.py --realm YOURREALM.quickbase.com --yes
   ```

   This creates one new app with the four tables, the 200 S01 records and their 80 files. It prints the app ID and
   whether every field ID equals the demo mapping's, and writes `local/quickbase-live.yml` and
   `local/qb-capture.yml`.
4. Continue with step 7.

The tool only creates, and only inside the app it creates (SPEC §22.17). If a build stops part way, the message
names the app: delete that app in Quickbase, then run the command again. The tool never deletes anything.

Two things differ from a manual build:
- **Approvals** are decided by your own user.
- **The attestation** in `local/qb-capture.yml` is true for the token that built the app. For the least-privilege
  setup of Appendix D, create the role and token of step 5, then update `operator_attestation`.

## 1. Generate the import files

```bash
uv run python tools/make_fixtures.py --quickbase-import local/qb-import --decided-by you@example.com
```

`--decided-by` must be a user of your realm, because a User field accepts only realm users. The command writes
`obligations.csv`, `inspections.csv`, `artifacts.csv` and `approvals.csv`, plus `files/O-nnn/report.pdf` and
`files/O-nnn/photo.png`.

## 2. Build the app (Appendix D)

1. Create the four tables, and their fields **in the order Appendix D lists**. New fields take the next free ID
   from 6, so this order reproduces the demo mapping's field IDs.
2. Create each relationship from the parent table. In that dialog, add **no** lookup or summary fields.

## 3. Import the data

1. Import into the new, empty tables in this order: obligations, inspections, artifacts, approvals.
2. Map each "Related …" column to the reference field that the relationship created.
3. The reference values assume that each table numbers its records 1, 2, … in import order. If a table ever held
   records, even deleted ones, create it again.
4. Check that **Completed At** and **Decided At** are filled. If the import does not parse a date, the field
   stays empty. That shows up later as R0 `COMPLETED_WITHOUT_TIMESTAMP` or `INVALID_VALUE`.
5. Quickbase reads imported times in the app's time zone. A uniform shift changes no finding.

## 4. Upload the attachments

For each artifact, upload its file to the **File** field:
- `ART-nnn-R` gets `files/O-nnn/report.pdf`;
- `ART-nnn-P` gets `files/O-nnn/photo.png`.

All 80 files reproduce S01 exactly. Any obligation whose files you skip shows R5 `FILE_ABSENT` (a FAIL) instead
of PASS. That is correct behavior, not a defect.

## 5. Grant read-only access

1. Create the role **Reconcile Reader**: view on all four tables, no add, modify or delete, and no admin rights.
2. Assign the role to the user who will own the token.
3. Create a user token for that user, assigned to this app only.
4. Put the token in the environment. Never put it in a file or on a command line.

PowerShell:

```powershell
$env:QB_USER_TOKEN = Read-Host -MaskInput "Quickbase user token"
```

## 6. Describe your app

1. Copy `mappings/quickbase-demo.yml` to `local/quickbase-live.yml`.
2. Set `mapping_id: quickbase-live`.
3. Replace each `table_id` with your table's ID. It is the `dbid` in the table's URL.
4. Keep the field IDs as they are. The capture verifies every field ID, type and mode against `GET /fields`, and
   stops with a run error that names any difference.

Then:

1. Copy `docs/qb-capture.example.yml` to `local/qb-capture.yml`.
2. Set `realm_hostname`, `app_id` and the attestation.
3. Attest `full_read_access: true` only if the role reads every record of the four tables.

## 7. Capture, assess, compare

```bash
uv run inspection-reconcile capture-quickbase --config local/qb-capture.yml --mapping local/quickbase-live.yml --out local/c3/export
uv run inspection-reconcile assess --export local/c3/export --mapping local/quickbase-live.yml --policy policies/north-creek-demo.yml --out local/c3/live
uv run inspection-reconcile assess --snapshot fixtures/scenarios/S01-clean/snapshot --policy policies/north-creek-demo.yml --out local/c3/s01
uv run inspection-reconcile compare --before local/c3/s01 --after local/c3/live --out local/c3/compare.json
```

To run this step again, add `--force` to each command. Without it, the commands refuse to replace their earlier
output (`OUT_NOT_EMPTY`, or `OUT_EXISTS` for `compare --out`).

**Expected result.** The live assessment is `READY_FOR_REVIEW` (exit 0). `compare` exits 20 even so: the
coverage basis and other capture-dependent values differ by design (D-005), and so does `evaluation_id`. The check
is that no finding was added or removed, and that none changed its outcome or reason:

```bash
uv run python -c "import json; c = json.load(open('local/c3/compare.json')); print(c['added'], c['removed'], [x['key'] for x in c['changed'] if x['before'] != x['after']])"
```

This prints `[] [] []`.

## 8. Record the result

- **What to record:**
  - the date;
  - the paging mode in `local/c3/export/capture-manifest.json` (`tables.*.paging`, `keyset` or `skip`);
  - every difference from Appendix D.

  Put them in `docs/limitations.md`, set the C3 row in `docs/status.md`, and reconcile SPEC §12.1 with what the
  realm did.
- **Before you commit anything captured:**
  - replace the realm hostname and the app and table IDs;
  - replace every user's name, email, user name and user ID **anywhere** in the capture. That means the User
    fields, and also each file version's `creator` and the attestation's `attested_by`. With the fast path, the
    token's user appears in all of them;
  - keep the unsanitized capture in `local/`.

  The spec asks that the sanitized responses, normalized again, reproduce the same snapshot.
