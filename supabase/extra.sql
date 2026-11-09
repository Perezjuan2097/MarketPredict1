-- Ejecutar UNA VEZ en Supabase (SQL Editor) DESPUÉS de crear las tablas con Alembic.
-- NO PROBADO contra PostgreSQL en el entorno de desarrollo: revíselo antes de ejecutarlo.

-- 1) Row Level Security en todas las tablas públicas.
--    El backend se conecta con el rol `postgres` (que omite RLS), así que la app sigue funcionando;
--    la clave pública (sb_publishable_...) NO podrá leer ni escribir nada por la API REST de Supabase.
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
  END LOOP;
END $$;

-- 2) Las predicciones públicas de usuarios no pueden alterarse retroactivamente.
CREATE OR REPLACE FUNCTION public.prevent_prediction_tampering() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.source = 'user' THEN
      RAISE EXCEPTION 'Las predicciones publicadas no se pueden eliminar; use el estado invalidada.';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.source = 'user' AND (
       NEW.user_id IS DISTINCT FROM OLD.user_id OR NEW.asset_id IS DISTINCT FROM OLD.asset_id
    OR NEW.direction IS DISTINCT FROM OLD.direction OR NEW.reference_price IS DISTINCT FROM OLD.reference_price
    OR NEW.target_price IS DISTINCT FROM OLD.target_price OR NEW.expected_return_pct IS DISTINCT FROM OLD.expected_return_pct
    OR NEW.horizon_days IS DISTINCT FROM OLD.horizon_days OR NEW.published_at IS DISTINCT FROM OLD.published_at
    OR NEW.target_date IS DISTINCT FROM OLD.target_date OR NEW.rationale IS DISTINCT FROM OLD.rationale
  ) THEN
    RAISE EXCEPTION 'Los campos de una predicción publicada no pueden modificarse.';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_prediction_tampering ON public.predictions;
CREATE TRIGGER trg_prevent_prediction_tampering
  BEFORE UPDATE OR DELETE ON public.predictions
  FOR EACH ROW EXECUTE FUNCTION public.prevent_prediction_tampering();
