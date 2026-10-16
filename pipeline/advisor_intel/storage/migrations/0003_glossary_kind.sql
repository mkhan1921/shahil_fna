-- The lexicon's glossary entries carry the catalogue kind of the resolved term (fund, policy,
-- provider …) beside its FNA product type (§8.5); keys are the lexicon's canonical form.
ALTER TABLE glossary ADD COLUMN kind text NOT NULL DEFAULT '';
