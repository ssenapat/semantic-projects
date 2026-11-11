-- id: V04_category_allocation
-- description: Every film has a category and equal-allocation weights sum to 1 per film (spec 7.4).
-- severity: error
SELECT 'films with no category' AS label,
       (SELECT COUNT(*) FROM public.film f WHERE NOT EXISTS (SELECT 1 FROM public.film_category fc WHERE fc.film_id = f.film_id)) AS actual,
       0 AS expected, 0 AS tolerance
UNION ALL SELECT 'films with more than one category',
       (SELECT COUNT(*) FROM (SELECT film_id FROM public.film_category GROUP BY film_id HAVING COUNT(*) > 1) x), 964, 0
UNION ALL SELECT 'films whose weights do not sum to 1',
       (SELECT COUNT(*) FROM (SELECT film_id, SUM(weight) AS s FROM (SELECT film_id, 1.0 / COUNT(*) OVER (PARTITION BY film_id) AS weight FROM public.film_category) w GROUP BY film_id) x WHERE ABS(s - 1) > 0.000001), 0, 0
