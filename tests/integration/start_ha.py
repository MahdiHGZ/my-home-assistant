"""Generate disposable Home Assistant and Mosquitto CI configuration."""
import json
from pathlib import Path
import sys

config = Path(sys.argv[1])
(config / ".storage").mkdir(parents=True)
(config / "mosquitto.conf").write_text("listener 1883\nallow_anonymous true\n")
entry = {"version": 2, "minor_version": 1, "entry_id": "01JCFY7RM8RS29TNTP4CJQ6PV6",
         "domain": "mqtt", "title": "Disposable broker", "data": {"broker": "127.0.0.1", "port": 1883},
         "options": {}, "source": "user", "unique_id": None, "disabled_by": None,
         "discovery_keys": {}, "subentries": [], "created_at": "2025-01-01T00:00:00+00:00",
         "modified_at": "2025-01-01T00:00:00+00:00", "pref_disable_new_entities": False,
         "pref_disable_polling": False}
(config / ".storage/core.config_entries").write_text(json.dumps({
    "version": 1, "minor_version": 5, "key": "core.config_entries", "data": {"entries": [entry]}}))
(config / "configuration.yaml").write_text("""
homeassistant:
  name: Coukab CI
  time_zone: UTC
automation:
  - alias: Report discovered Coukab entities
    triggers:
      - trigger: mqtt
        topic: coukab-test/query
    actions:
      - action: mqtt.publish
        data:
          topic: coukab-test/observed
          payload: >-
            {% set result = namespace(sensors=[], buttons=[]) %}
            {% for entity in states.sensor if entity.name in ['Temperature', 'Coukab Temperature'] %}
              {% set result.sensors = result.sensors + [{
                'id': entity.entity_id, 'state': entity.state,
                'unit': entity.attributes.get('unit_of_measurement')
              }] %}
            {% endfor %}
            {% for entity in states.button if entity.name in ['Evening', 'Coukab Evening'] %}
              {% set result.buttons = result.buttons + [{'id': entity.entity_id, 'state': entity.state}] %}
            {% endfor %}
            {{ {'request': trigger.payload, 'sensors': result.sensors, 'buttons': result.buttons} | to_json }}
  - alias: Press the discovered Coukab routine button
    triggers:
      - trigger: mqtt
        topic: coukab-test/press
    conditions:
      - condition: template
        value_template: "{{ trigger.payload.startswith('button.') }}"
    actions:
      - action: button.press
        target:
          entity_id: "{{ trigger.payload }}"
""")
