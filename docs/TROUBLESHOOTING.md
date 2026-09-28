# Risoluzione dei Problemi

Questa guida copre i problemi comuni riscontrati durante l'utilizzo di Laya Pro.

## Errore: JEVUnavailable
- **Causa**: Il backend JEV (System 1) non è attualmente connesso o configurato correttamente.
- **Soluzione**: Al momento, assicurati di utilizzare la modalità AI **LOW**. Le modalità MEDIUM e HARD causeranno questo errore finché JEV non sarà pienamente integrato.

## Problemi di Avvio (Windows)
- **Causa**: L'esecuzione degli script potrebbe essere bloccata.
- **Soluzione**: Verifica di aver eseguito lo script di avvio con \-ExecutionPolicy Bypass\.

## Missing Dependency: laya-coreml
- **Causa**: Mancata installazione del bridge CPU per Windows.
- **Soluzione**: Riavvia lo script di installazione. Ricorda che su Windows, Core ML opera tramite un bridge PyTorch simulato e non nativamente.
