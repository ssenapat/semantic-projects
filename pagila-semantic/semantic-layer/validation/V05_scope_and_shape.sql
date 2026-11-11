-- id: V05_scope_and_shape
-- description: Stores and staff in use, payment partitions, rating labels, title uniqueness (spec 6.1, 7.5).
-- severity: error
SELECT 'stores holding inventory' AS label,
       (SELECT COUNT(DISTINCT store_id) FROM public.inventory) AS actual, 2 AS expected, 0 AS tolerance
UNION ALL SELECT 'store_in_use rows',
       (SELECT COUNT(*) FROM (SELECT s.store_id, s.manager_staff_id, s.address_id FROM public.store s WHERE EXISTS (SELECT 1 FROM public.inventory i WHERE i.store_id = s.store_id)) x), 2, 0
UNION ALL SELECT 'staff_in_use rows',
       (SELECT COUNT(*) FROM (SELECT st.staff_id, st.first_name, st.last_name, st.store_id, st.active FROM public.staff st WHERE EXISTS (SELECT 1 FROM public.rental r WHERE r.staff_id = st.staff_id) OR EXISTS (SELECT 1 FROM public.payment p WHERE p.staff_id = st.staff_id)) x), 2, 0
UNION ALL SELECT 'payment partitions that hold rows',
       (SELECT COUNT(DISTINCT tableoid) FROM public.payment), 55, 0
UNION ALL SELECT 'distinct film ratings',
       (SELECT COUNT(DISTINCT rating) FROM public.film), 5, 0
UNION ALL SELECT 'duplicate film titles',
       (SELECT COUNT(*) - COUNT(DISTINCT title) FROM public.film), 0, 0
UNION ALL SELECT 'rentals on inventory not in stores 1 or 2',
       (SELECT COUNT(*) FROM public.rental r JOIN public.inventory i ON i.inventory_id = r.inventory_id WHERE i.store_id NOT IN (1, 2)), 0, 0
