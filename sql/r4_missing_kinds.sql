WITH cand AS (
  SELECT o.obligation_id, i.inspection_id, i.activity_kind
  FROM obligations o JOIN inspections i ON i.obligation_id = o.obligation_id AND i.is_current = 1
  WHERE (SELECT COUNT(*) FROM inspections x WHERE x.obligation_id = o.obligation_id AND x.is_current = 1) = 1
    AND i.completion_status = 'completed')
SELECT c.obligation_id, rk.document_kind AS missing_kind
FROM cand c JOIN required_kinds rk ON rk.activity_kind = c.activity_kind
WHERE NOT EXISTS (SELECT 1 FROM artifacts a WHERE a.inspection_id = c.inspection_id AND a.is_current = 1
                  AND a.document_kind = rk.document_kind)
ORDER BY 1, 2;
