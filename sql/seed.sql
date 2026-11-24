-- Synthetic sample data (no real patients).
INSERT INTO patients (id, name, dob, plan) VALUES
    (77,  'Patient A', '1958-03-14', 'Medicare Advantage'),
    (78,  'Patient B', '1990-11-02', 'Commercial PPO'),
    (79,  'Patient C', '1972-06-25', 'Medicaid')
ON CONFLICT (id) DO NOTHING;

INSERT INTO orders (id, patient_id, code, description, amount, ordered_at) VALUES
    (1181, 77, '99214', 'Office visit, established patient, moderate complexity', 185.00,  '2026-10-02'),
    (1182, 77, '93000', 'Electrocardiogram, 12-lead, with interpretation',       75.00,   '2026-10-02'),
    (1183, 77, '78452', 'Myocardial perfusion imaging, multiple studies',        1450.00, '2026-10-09'),
    (1190, 78, '99213', 'Office visit, established patient, low complexity',     120.00,  '2026-10-15'),
    (1191, 78, '87880', 'Rapid strep test',                                      35.00,   '2026-10-15'),
    (1200, 79, '99285', 'Emergency department visit, high severity',             2400.00, '2026-10-20'),
    (1201, 79, '71046', 'Chest X-ray, 2 views',                                  210.00,  '2026-10-20'),
    (1202, 79, '70450', 'CT head without contrast',                              980.00,  '2026-10-20')
ON CONFLICT (id) DO NOTHING;

INSERT INTO claims (id, patient_id, order_ids, status, submitted_at) VALUES
    (1, 77, '{1181,1182}',      'submitted', '2026-10-05'),
    (2, 77, '{1183}',           'pending_review', '2026-10-12'),
    (3, 78, '{1190,1191}',      'submitted', '2026-10-18'),
    (4, 79, '{1200,1201,1202}', 'pending_review', '2026-10-22')
ON CONFLICT (id) DO NOTHING;
