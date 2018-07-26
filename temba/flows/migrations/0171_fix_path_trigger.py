# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2018-07-26 16:13
from __future__ import unicode_literals

from django.db import migrations

SQL = """
----------------------------------------------------------------------
-- Handles changes relating to a flow run's path
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_flowrun_path_change() RETURNS TRIGGER AS $$
DECLARE
  p INT;
  s JSONB;
  _old_path_json JSONB;
  _new_path_json JSONB;
  _old_path_len INT;
  _new_path_len INT;
  _old_last_step_uuid TEXT;
  _new_last_step_uuid TEXT;
BEGIN
    -- restrict changes
    IF NEW.is_active AND NOT OLD.is_active THEN RAISE EXCEPTION 'Cannot re-activate an inactive flow run'; END IF;

    -- ignore test contacts
    IF temba_contact_is_test(NEW.contact_id) THEN RETURN NULL; END IF;

    _old_path_json := COALESCE(OLD.path, '[]')::jsonb;
    _new_path_json := COALESCE(NEW.path, '[]')::jsonb;
    _old_path_len := jsonb_array_length(_old_path_json);
    _new_path_len := jsonb_array_length(_new_path_json);

    -- we don't support rewinding run paths, so the new path must be longer than the old
    IF _new_path_len < _old_path_len THEN RAISE EXCEPTION 'Cannot rewind a flow run path'; END IF;

    -- update the node counts
    IF _old_path_len > 0 AND OLD.is_active THEN
        PERFORM temba_insert_flownodecount(OLD.flow_id, UUID(_old_path_json->(_old_path_len-1)->>'node_uuid'), -1);
    END IF;

    IF _new_path_len > 0 AND NEW.is_active THEN
        PERFORM temba_insert_flownodecount(NEW.flow_id, UUID(_new_path_json->(_new_path_len-1)->>'node_uuid'), 1);
    END IF;

    -- if we have an old path, find its last step in the new path, and that will be our starting point
    IF _old_path_len > 1 THEN
        _old_last_step_uuid := _old_path_json->(_old_path_len-1)->>'uuid';
        _new_last_step_uuid := _new_path_json->(_new_path_len-1)->>'uuid';

        -- old and new paths end with same step so path activity doesn't change
        IF _old_last_step_uuid = _new_last_step_uuid THEN
            RETURN NULL;
        END IF;

        p := _new_path_len - 1;
        LOOP
            EXIT WHEN p = 1 OR _new_path_json->(p-1)->>'uuid' = _old_last_step_uuid;
            p := p - 1;
        END LOOP;
    ELSE
        p := 1;
    END IF;

    LOOP
      EXIT WHEN p >= _new_path_len;
      PERFORM temba_insert_flowpathcount(
          NEW.flow_id,
          UUID(_new_path_json->(p-1)->>'exit_uuid'),
          UUID(_new_path_json->p->>'node_uuid'),
          timestamptz(_new_path_json->p->>'arrived_on'),
          1
      );
      PERFORM temba_insert_flowpathrecentrun(
          UUID(_new_path_json->(p-1)->>'exit_uuid'),
          UUID(_new_path_json->(p-1)->>'uuid'),
          UUID(_new_path_json->p->>'node_uuid'),
          UUID(_new_path_json->p->>'uuid'),
          NEW.id,
          timestamptz(_new_path_json->p->>'arrived_on')
      );
      p := p + 1;
    END LOOP;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Handles inserting new flow runs
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_flowrun_insert() RETURNS TRIGGER AS $$
DECLARE
    p INT;
    _path_json JSONB;
    _path_len INT;
BEGIN
    -- nothing to do if path is empty
    IF NEW.path IS NULL OR NEW.path = '[]' THEN RETURN NULL; END IF;

    -- ignore test contacts
    IF temba_contact_is_test(NEW.contact_id) THEN RETURN NULL; END IF;

    -- parse path as JSON
    _path_json := NEW.path::json;
    _path_len := jsonb_array_length(_path_json);

    -- increment node count at last node in this path if this is an active run
    IF _path_len > 0 AND NEW.is_active THEN
        PERFORM temba_insert_flownodecount(NEW.flow_id, UUID(_path_json->(_path_len-1)->>'node_uuid'), 1);
    END IF;

    -- for each step in the path, increment the path count, and record a recent run
    p := 1;
    LOOP
        EXIT WHEN p >= _path_len;

        PERFORM temba_insert_flowpathcount(
            NEW.flow_id,
            UUID(_path_json->(p-1)->>'exit_uuid'),
            UUID(_path_json->p->>'node_uuid'),
            timestamptz(_path_json->p->>'arrived_on'),
            1
        );
        PERFORM temba_insert_flowpathrecentrun(
            UUID(_new_path_json->(p-1)->>'exit_uuid'),
            UUID(_new_path_json->(p-1)->>'uuid'),
            UUID(_new_path_json->p->>'node_uuid'),
            UUID(_new_path_json->p->>'uuid'),
            NEW.id,
            timestamptz(_new_path_json->p->>'arrived_on')
        );

        p := p + 1;
    END LOOP;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Handles deleting flow runs
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_flowrun_delete() RETURNS TRIGGER AS $$
DECLARE
    p INT;
    _path_json JSONB;
    _path_len INT;
BEGIN
    -- leave activity as is if we're being archived or we're a test contact
    IF OLD.delete_reason = 'A' OR temba_contact_is_test(OLD.contact_id) THEN
        RETURN NULL;
    END IF;

    -- nothing to do if path was empty
    IF OLD.path IS NULL OR OLD.path = '[]' THEN RETURN NULL; END IF;

    -- parse path as JSON
    _path_json := OLD.path::json;
    _path_len := jsonb_array_length(_path_json);

    -- decrement node count at last node in this path if this was an active run
    IF OLD.is_active THEN
        PERFORM temba_insert_flownodecount(OLD.flow_id, UUID(_path_json->(_path_len-1)->>'node_uuid'), -1);
    END IF;

    -- for each step in the path, decrement the path count
    p := 1;
    LOOP
        EXIT WHEN p >= _path_len;

        -- it's possible that steps from old flows don't have exit_uuid
        IF (_path_json->(p-1)->'exit_uuid') IS NOT NULL THEN
            PERFORM temba_insert_flowpathcount(
                OLD.flow_id,
                UUID(_path_json->(p-1)->>'exit_uuid'),
                UUID(_path_json->p->>'node_uuid'),
                timestamptz(_path_json->p->>'arrived_on'),
                -1
            );
        END IF;

        p := p + 1;
    END LOOP;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;


DROP TRIGGER temba_flowrun_path_change ON flows_flowrun;
CREATE TRIGGER temba_flowrun_path_change
    AFTER UPDATE OF path, is_active ON flows_flowrun
    FOR EACH ROW EXECUTE PROCEDURE temba_flowrun_path_change();

CREATE TRIGGER temba_flowrun_insert
    AFTER INSERT ON flows_flowrun
    FOR EACH ROW EXECUTE PROCEDURE temba_flowrun_insert();

CREATE TRIGGER temba_flowrun_delete
    AFTER DELETE ON flows_flowrun
    FOR EACH ROW EXECUTE PROCEDURE temba_flowrun_delete();
"""


class Migration(migrations.Migration):

    dependencies = [("flows", "0170_triggers")]

    operations = [migrations.RunSQL(SQL)]
