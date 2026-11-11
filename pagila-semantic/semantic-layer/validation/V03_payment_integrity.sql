-- id: V03_payment_integrity
-- description: Payment/rental relationship quirks documented in spec 7.3 and open/closed rentals in 7.1.
-- severity: error
SELECT 'payments with no rental' AS label,
       (SELECT COUNT(*) FROM public.payment p LEFT JOIN public.rental r ON r.rental_id = p.rental_id WHERE r.rental_id IS NULL) AS actual,
       0 AS expected, 0 AS tolerance
UNION ALL SELECT 'rentals with no payment',
       (SELECT COUNT(*) FROM public.rental r WHERE NOT EXISTS (SELECT 1 FROM public.payment p WHERE p.rental_id = r.rental_id)), 749, 0
UNION ALL SELECT 'rentals with more than one payment',
       (SELECT COUNT(*) FROM (SELECT rental_id FROM public.payment GROUP BY rental_id HAVING COUNT(*) > 1) x), 1, 0
UNION ALL SELECT 'payments whose customer differs from the rental customer',
       (SELECT COUNT(*) FROM public.payment p JOIN public.rental r ON r.rental_id = p.rental_id WHERE p.customer_id <> r.customer_id), 5, 0
UNION ALL SELECT 'zero-value payments', (SELECT COUNT(*) FROM public.payment WHERE amount = 0), 24, 0
UNION ALL SELECT 'returned before rented', (SELECT COUNT(*) FROM public.rental WHERE return_date < rental_date), 0, 0
UNION ALL SELECT 'open rentals', (SELECT COUNT(*) FROM public.rental WHERE return_date IS NULL), 241, 0
UNION ALL SELECT 'payments dated before their rental (informational, spec 7.2.1)',
       (SELECT COUNT(*) FROM public.payment p JOIN public.rental r ON r.rental_id = p.rental_id WHERE p.payment_date < r.rental_date), 14665, 0
