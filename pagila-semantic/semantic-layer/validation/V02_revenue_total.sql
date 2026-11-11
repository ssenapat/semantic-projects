-- id: V02_revenue_total
-- description: Total revenue and payment count match the baseline in spec 6.4.
-- severity: error
SELECT 'total payment amount' AS label, (SELECT SUM(amount) FROM public.payment) AS actual, 170962.39 AS expected, 0.005 AS tolerance
UNION ALL SELECT 'payment count', (SELECT COUNT(*) FROM public.payment), 51061, 0
UNION ALL SELECT 'max payment', (SELECT MAX(amount) FROM public.payment), 11.99, 0
