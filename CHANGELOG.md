# Changelog

Tutte le modifiche rilevanti di Laya Pro sono documentate qui.
Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/).

## [Unreleased]

### Aggiunto
- Orchestratore ibrido con tre modalita': LOW (solo Nemotron), MEDIUM (piano + risposta)
  e HARD (piano + risposta + revisione con critico).
- Contratti tipizzati e fail-closed per i due motori, con `trace` per hop in ogni risposta.
- UI ChatGPT-style: sidebar delle conversazioni, selettore di modalita', pipeline espandibile,
  pannello memoria, tema chiaro/scuro, responsive.
- Memoria su SQLite: estrazione automatica dei fatti, retrieval lessicale, voci pinnate.
- `run.py`, un launcher che funziona da qualsiasi directory, con `--with-stub`, `--reload`,
  `--host` e `--port`. `start.bat` per Windows.
- `tools/laya_coreml_stub.py`: stand-in del runtime di ragionamento, conforme al contratto HTTP.
- Documentazione in `docs/` con immagini e README ampio.
- 106 test e CI su Python 3.9 con smoke test del launcher.

### Corretto
- Il prefisso `LAYA_` impediva la lettura di `NEMOTRON_API_KEY`: ogni campo accetta ora i
  prefissi `NEMOTRON_*`, `LAYA_*` e `COREML_*` (con regressione dedicata).
- Deadlock in `RuntimeObservation.snapshot()`: Lock annidato sostituito con RLock, con pool
  di latenze separati per motore.
- `/status` riportava `configured: true` anche senza chiave.
- `LayaRefusal` non aveva handler e produceva `500`: ora e' `422` con motivo e suggerimento.
- JSON troncato dal planner: ora viene salvato il prefisso valido invece di rifiutare tutto.
- Risposta con coda del chat template (`EOS`, `</think>UUID.`) ripulita prima di mostrarla
  all'utente; se resta solo rumore, diventa errore di contratto.
- Il banner di avviso e i titoli della sidebar non comparivano correttamente.

### Rimosso
- Il vecchio backend (sandbox, approvazioni, workflow, audit, orchestratore JEV): non usava
  mai il proprio runtime di ragionamento. Sostituito dal nucleo ibrido descritto sopra.
