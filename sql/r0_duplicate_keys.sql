SELECT 'inspection' AS entity, inspection_id || '@' || revision AS key, COUNT(*) AS n FROM inspections GROUP BY inspection_id, revision HAVING n > 1
UNION ALL SELECT 'artifact', artifact_id || '@' || revision, COUNT(*) FROM artifacts GROUP BY artifact_id, revision HAVING COUNT(*) > 1
UNION ALL SELECT 'approval', approval_id, COUNT(*) FROM approvals GROUP BY approval_id HAVING COUNT(*) > 1
UNION ALL SELECT 'scope_row', obligation_id, COUNT(*) FROM obligations GROUP BY obligation_id HAVING COUNT(*) > 1
ORDER BY 1, 2;
