// Pont WhatsApp ⇄ Agent avocat (Baileys : protocole WhatsApp Web, sans navigateur).
//
// Deux modes :
//  • « moi-même » (défaut) : le pont utilise TON WhatsApp ; l'agent t'écrit dans « Message à moi-même ».
//  • « numéro bot » : le pont tourne sur un 2e numéro ; mets ton numéro perso dans WHATSAPP_MON_NUMERO
//    et l'agent t'écrit comme un vrai contact (recommandé).
//
// Lancé seul (`npm start`) ou par l'application, qui lit le QR code sur GET /etat.
// Dossiers : AGENT_STUDENT_RACINE (fourni par l'application) sinon le dossier parent.
const B = require('@whiskeysockets/baileys');
const qrcodeTerminal = require('qrcode-terminal');
const express = require('express');
const pino = require('pino');
const fs = require('node:fs');
const path = require('node:path');

const racine = process.env.AGENT_STUDENT_RACINE || path.join(__dirname, '..');
const fichierEnv = path.join(racine, '.env');
if (fs.existsSync(fichierEnv)) {
  for (const l of fs.readFileSync(fichierEnv, 'utf8').split(/\r?\n/)) {
    const m = l.match(/^\s*([A-Z0-9_]+)\s*=\s*([^#]*?)\s*(#.*)?$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^['"]|['"]$/g, '');
  }
}

const makeWASocket = B.makeWASocket || B.default;
const { useMultiFileAuthState, DisconnectReason, downloadMediaMessage, fetchLatestBaileysVersion } = B;

const AGENT = `http://127.0.0.1:${process.env.PORT_AGENT || 3021}/entrant`;
const PORT = Number(new URL(process.env.WHATSAPP_PONT_URL || 'http://127.0.0.1:3020').port || 3020);
const SESSION = path.join(racine, 'donnees', 'whatsapp_session');
const RECUS = path.join(racine, 'donnees', 'recus');
fs.mkdirSync(RECUS, { recursive: true });
const logger = pino({ level: 'warn' });

const nu = jid => (jid || '').split('@')[0].split(':')[0];
let sock, cible, moi = new Set();
const etat = { connecte: false, qr: null, numero: null, mode: null, erreur: null };
const envoyes = new Set(); // ids de nos propres messages, pour ne pas se répondre à soi-même

async function connecter() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION);
  const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: undefined }));
  sock = makeWASocket({ auth: state, version, logger, markOnlineOnConnect: false, syncFullHistory: false });
  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      etat.qr = qr;
      console.log('\nScanne ce QR code avec WhatsApp > Appareils connectés :\n');
      qrcodeTerminal.generate(qr, { small: true });
    }
    if (connection === 'open') {
      moi = new Set([`${nu(sock.user.id)}@s.whatsapp.net`, sock.user.lid ? `${nu(sock.user.lid)}@lid` : ''].filter(Boolean));
      const perso = (process.env.WHATSAPP_MON_NUMERO || '').replace(/\D/g, '');
      cible = perso && perso !== nu(sock.user.id) ? `${perso}@s.whatsapp.net` : `${nu(sock.user.id)}@s.whatsapp.net`;
      Object.assign(etat, { connecte: true, qr: null, numero: nu(sock.user.id),
                            mode: moi.has(cible) ? 'moi-meme' : 'numero-bot', erreur: null });
      console.log(`✅ WhatsApp connecté (${etat.mode})`);
    }
    if (connection === 'close') {
      etat.connecte = false;
      const code = lastDisconnect?.error?.output?.statusCode;
      if (code === DisconnectReason.loggedOut) {
        etat.erreur = 'deconnecte';
        fs.rmSync(SESSION, { recursive: true, force: true });   // prochain lancement : nouveau QR
        setTimeout(connecter, 2000);
      } else setTimeout(connecter, 3000);
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify' && type !== 'append') return;
    for (const msg of messages) {
      const jid = msg.key.remoteJid;
      if (!msg.message || envoyes.has(msg.key.id)) continue;
      const modeMoi = moi.has(cible);
      const pourNous = modeMoi ? (moi.has(jid) && msg.key.fromMe) : (nu(jid) === nu(cible) && !msg.key.fromMe);
      if (!pourNous) continue;
      const c = msg.message.ephemeralMessage?.message || msg.message;
      const corps = { texte: c.conversation || c.extendedTextMessage?.text || c.documentMessage?.caption || '' };
      try {
        if (c.audioMessage) corps.audio = await sauver(msg, 'vocal.ogg');
        if (c.documentMessage) {
          corps.nom_fichier = c.documentMessage.fileName || 'document.pdf';
          corps.fichier = await sauver(msg, corps.nom_fichier);
        }
        if (!corps.texte && !corps.audio && !corps.fichier) continue;
        await fetch(AGENT, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corps) });
      } catch (e) { console.error('Message non transmis :', e.message); }
    }
  });
}

async function sauver(msg, nom) {
  const buf = await downloadMediaMessage(msg, 'buffer', {}, { logger, reuploadRequest: sock.updateMediaMessage });
  const f = path.join(RECUS, `${Date.now()}_${nom.replace(/[^\w.\-]/g, '_')}`);
  fs.writeFileSync(f, buf);
  return f;
}

async function envoyer(contenu) {
  if (!sock || !cible || !etat.connecte) throw new Error('WhatsApp pas encore connecté');
  const r = await sock.sendMessage(cible, contenu);
  envoyes.add(r.key.id);
  if (envoyes.size > 500) envoyes.delete(envoyes.values().next().value);
}

const app = express();
app.use(express.json({ limit: '2mb' }));
app.get('/etat', (_req, res) => res.json(etat));
app.post('/envoyer', async (req, res) => {
  try { await envoyer({ text: req.body.texte }); res.send('ok'); } catch (e) { res.status(503).send(e.message); }
});
app.post('/fichier', async (req, res) => {
  try {
    const f = req.body.chemin;
    const ext = path.extname(f).toLowerCase();
    const mimetype = { '.pdf': 'application/pdf', '.ics': 'text/calendar', '.txt': 'text/plain' }[ext] || 'application/octet-stream';
    await envoyer({ document: fs.readFileSync(f), mimetype, fileName: path.basename(f), caption: req.body.legende || '' });
    res.send('ok');
  } catch (e) { res.status(503).send(e.message); }
});
app.listen(PORT, '127.0.0.1', () => console.log(`Pont WhatsApp prêt sur le port ${PORT}`));
connecter().catch(e => { etat.erreur = e.message; console.error(e); });
