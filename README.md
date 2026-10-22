# Automation Replay

Rejoue (replay) une automatisation Home Assistant en réinjectant l'objet
**trigger** capturé dans une de ses traces — ce que le bouton « Exécuter »
natif jette à la poubelle — ou avec un payload trigger custom.

Inspiré du « replay » de n8n, pour un usage **manuel et ponctuel** (debug,
rejeu d'un événement raté).

## Installation (HACS)

1. HACS → Intégrations → menu ⋮ → *Dépôts personnalisés*
2. Ajouter `rdorys/ha-traces-replay`, catégorie **Integration**
3. Installer **Automation Replay**, puis **redémarrer** Home Assistant

## Utilisation

Outils de développement → **Actions** → `automation_replay.replay`.

### Rejouer depuis une trace

```yaml
action: automation_replay.replay
data:
  entity_id: automation.ma_super_automatisation
  # run_id optionnel : vide = dernière trace
  run_id: 0b873bb037bb0944339f143460bd3f50
  skip_condition: true   # comme le bouton "Exécuter" ; false = évalue les conditions
```

### Rejouer avec un trigger custom

```yaml
action: automation_replay.replay
data:
  entity_id: automation.ma_super_automatisation
  trigger:
    platform: state
    entity_id: switch.x
    from_state: "on"
    to_state: "off"
```

Avec « Répondre », l'action renvoie l'`entity_id`, le `run_id` et le `trigger`
réellement utilisés.

## Portée (v1) et limites assumées

- Seul le `trigger` est restauré. Le **contexte d'origine n'est pas copié**, et
  l'état du monde (capteurs, `states(...)`, `now()`) reste **l'état courant** :
  ce n'est pas une machine à remonter le temps, c'est un re-déclenchement avec
  le bon trigger.
- `from_state` / `to_state` sont la forme **réduite** stockée en trace (chaîne,
  sans attributs) : un template du type `trigger.to_state.attributes.xxx` ne
  retrouvera pas l'objet `State` complet. Réhydratation prévue en v2.
- Rejouer **ré-exécute réellement** les actions (lumières, notifications…).

## Licence

MIT
