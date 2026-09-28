# Architettura di Laya Pro

Laya Pro adotta un'architettura ibrida all'avanguardia che unisce un sistema decisionale reattivo a un motore di ragionamento profondo.

## System 1 e System 2
- **System 1 (Laya local core / JEV)**: Gestisce flussi operativi immediati, logica decisionale strutturata e compiti deterministici. Attualmente è in fase di integrazione.
- **System 2 (Nemotron)**: Interviene per task analitici avanzati, generazione di risposte complesse ed esplorazione di query non deterministiche.

## Core ML
Core ML è supportato esclusivamente come tecnologia di esecuzione backend dove applicabile. Ad esempio, gli utenti macOS potrebbero teoricamente utilizzarlo in modo nativo, mentre su Windows le operazioni si basano su un bridge CPU PyTorch (tramite il modulo laya-coreml). **Nota:** Core ML non è disponibile nativamente su ambienti Windows.
