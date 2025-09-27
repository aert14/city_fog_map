-- Add geom column to user_visits_atomic table if it doesn't exist
-- This migration ensures the table has the geometry column needed for spatial operations

-- First, add the geom column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'user_visits_atomic'
                   AND column_name = 'geom') THEN
        ALTER TABLE user_visits_atomic ADD COLUMN geom GEOMETRY(Point, 4326);
    END IF;
END $$;

-- Create spatial index on the geom column
CREATE INDEX IF NOT EXISTS idx_user_visits_atomic_geom ON user_visits_atomic USING GIST(geom);

-- Update existing records to populate geom from h3 coordinates
-- This will backfill geometry data for existing records
-- Note: This backfill may be slow for large datasets and should be run manually if needed
-- For now, we'll leave existing records without geom (they will be updated when re-visited)