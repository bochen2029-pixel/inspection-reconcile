SELECT o.obligation_id,
       COUNT(i.inspection_id) AS current_count,
       CASE WHEN COUNT(i.inspection_id) = 0 THEN 'NO_CURRENT_INSPECTION'
            WHEN COUNT(i.inspection_id) > 1 THEN 'MULTIPLE_CURRENT'
            WHEN SUM(i.completion_status = 'completed') = 1 THEN 'SINGLE_CURRENT_COMPLETED'
            ELSE 'NOT_COMPLETED' END AS r2_reason
FROM obligations o
LEFT JOIN inspections i ON i.obligation_id = o.obligation_id AND i.is_current = 1
GROUP BY o.obligation_id
ORDER BY o.obligation_id;
