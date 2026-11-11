-- id: V01_row_counts
-- description: Row counts of every exposed table match the baseline in spec 6.1.
-- severity: error
SELECT 'rental' AS label, (SELECT COUNT(*) FROM public.rental) AS actual, 51805 AS expected, 0 AS tolerance
UNION ALL SELECT 'payment', (SELECT COUNT(*) FROM public.payment), 51061, 0
UNION ALL SELECT 'inventory', (SELECT COUNT(*) FROM public.inventory), 4581, 0
UNION ALL SELECT 'film', (SELECT COUNT(*) FROM public.film), 1000, 0
UNION ALL SELECT 'film_category', (SELECT COUNT(*) FROM public.film_category), 2367, 0
UNION ALL SELECT 'category', (SELECT COUNT(*) FROM public.category), 16, 0
UNION ALL SELECT 'film_actor', (SELECT COUNT(*) FROM public.film_actor), 5462, 0
UNION ALL SELECT 'actor', (SELECT COUNT(*) FROM public.actor), 200, 0
UNION ALL SELECT 'language', (SELECT COUNT(*) FROM public.language), 6, 0
UNION ALL SELECT 'customer', (SELECT COUNT(*) FROM public.customer), 999, 0
UNION ALL SELECT 'store', (SELECT COUNT(*) FROM public.store), 500, 0
UNION ALL SELECT 'staff', (SELECT COUNT(*) FROM public.staff), 1500, 0
UNION ALL SELECT 'address', (SELECT COUNT(*) FROM public.address), 1003, 0
UNION ALL SELECT 'city', (SELECT COUNT(*) FROM public.city), 600, 0
UNION ALL SELECT 'country', (SELECT COUNT(*) FROM public.country), 109, 0
