-- Run as an authorized schema owner, not the runtime app identity.
-- Additive, empty contract. Populate ONLY from actual dated source evidence.
-- No invented species occurrence or demonstration records.
CREATE TABLE IF NOT EXISTS bite_atlas.silver.scout_species_occurrence (
  occurrence_id STRING,
  waterbody_id STRING,
  species_name STRING,
  observed_at TIMESTAMP,
  evidence_type STRING,
  source_url STRING,
  source_id STRING,
  spatial_uncertainty_m DOUBLE,
  validation_status STRING,
  ingested_at TIMESTAMP
) USING DELTA;

CREATE VIEW IF NOT EXISTS bite_atlas.gold.api_scout_species_occurrence AS
SELECT occurrence_id, waterbody_id, species_name, observed_at,
       evidence_type, source_url, source_id, spatial_uncertainty_m, ingested_at
FROM bite_atlas.silver.scout_species_occurrence
WHERE validation_status = 'validated'
  AND source_url IS NOT NULL AND observed_at IS NOT NULL;

-- Grant the app SELECT on this Gold view and USE on its parent schema/catalog
-- if not already inherited. Keep ingestion write permissions on its own identity.
-- Inspect any preexisting objects before changing their schemas or definitions.
