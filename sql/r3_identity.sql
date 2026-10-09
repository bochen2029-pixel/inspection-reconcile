WITH cand AS (
  SELECT o.obligation_id, o.project_id AS o_project, o.asset_id AS o_asset, o.activity_kind AS o_activity,
         i.inspection_id, i.project_id AS i_project, i.asset_id AS i_asset, i.activity_kind AS i_activity
  FROM obligations o JOIN inspections i ON i.obligation_id = o.obligation_id AND i.is_current = 1
  WHERE (SELECT COUNT(*) FROM inspections x WHERE x.obligation_id = o.obligation_id AND x.is_current = 1) = 1)
SELECT obligation_id, 'inspection' AS subject, inspection_id AS subject_id, 'project_id' AS field FROM cand WHERE i_project <> o_project
UNION ALL SELECT obligation_id, 'inspection', inspection_id, 'asset_id' FROM cand WHERE i_asset <> o_asset
UNION ALL SELECT obligation_id, 'inspection', inspection_id, 'activity_kind' FROM cand WHERE i_activity <> o_activity
UNION ALL SELECT c.obligation_id, 'artifact', a.artifact_id, 'project_id' FROM cand c
          JOIN artifacts a ON a.inspection_id = c.inspection_id AND a.is_current = 1 WHERE a.project_id <> c.o_project
UNION ALL SELECT c.obligation_id, 'artifact', a.artifact_id, 'asset_id' FROM cand c
          JOIN artifacts a ON a.inspection_id = c.inspection_id AND a.is_current = 1 WHERE a.asset_id <> c.o_asset
ORDER BY 1, 2, 3, 4;
