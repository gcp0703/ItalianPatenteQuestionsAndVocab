# Quiz Patente B

I quiz sono aggiornati al 2023. Il file `quizPatenteB2023.json` contiene 7139 domande, di cui 3983 con immagini; le immagini si trovano nella cartella `img_sign`.

## Web app

L'applicazione espone:

- un backend Python/FastAPI che estrae 30 domande casuali, calcola il punteggio finale e richiede la traduzione inglese tramite Google Translate
- un frontend React/Vite che mostra le domande in italiano, l'immagine associata se presente, la selezione `Vero` / `Falso`, la navigazione avanti/indietro e il riepilogo finale

## Avvio locale

1. Creare un ambiente virtuale Python e installare le dipendenze:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Installare le dipendenze frontend:

```bash
cd frontend
npm install
```

3. Avviare il backend:

```bash
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8500
```

4. In un secondo terminale, avviare il frontend:

```bash
cd frontend
npm run dev -- --host 127.0.0.1 --port 5183
```

In alternativa, dopo aver installato le dipendenze, si possono riavviare entrambi i servizi con un solo comando:

```bash
./restart-dev.sh
```

Lo script arresta eventuali processi già in ascolto sulle porte di sviluppo, rilancia backend e frontend in background e salva log e PID nella cartella `.run/`.
L'app di sviluppo risponde su `http://127.0.0.1:5183` e il backend su `http://127.0.0.1:8500`.

## Build produzione

Per servire il frontend direttamente da FastAPI:

```bash
cd frontend
npm run build
cd ..
uvicorn backend.app.main:app --host 127.0.0.1 --port 8500
```

## Note

- Le immagini vengono servite dal backend sul path `/img_sign/...`.
- La traduzione inglese viene richiesta on demand quando si apre la sezione nascosta sotto la domanda in italiano.
- La traduzione usa Google Translate tramite la libreria `deep-translator`, quindi richiede connettività di rete.
