SELECT DISTINCT i.inspection_id
FROM inspections i
WHERE i.is_current = 1
  AND (i.obligation_id IS NULL OR i.obligation_id NOT IN (SELECT obligation_id FROM obligations))
ORDER BY 1;
