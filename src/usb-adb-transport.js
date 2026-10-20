/* AliançaDX USB installer transport. Only fixed app/install/cleanup operations. */
import { Adb, AdbDaemonTransport } from '@yume-chan/adb';
import { AdbDaemonWebUsbDeviceManager } from '@yume-chan/adb-daemon-webusb';

export const APK_PACKAGE = 'br.com.perabru.minianydesk.controlador';
export const APK_VERSION_CODE = 62000;
export const APK_SHA256 = '3ff081e7c6244be6c07d5a45d658f05c845d052bbc7064deec8b736e89379f3c';
export const APK_SIZE = 34699638;
const TEMP_APK = '/data/local/tmp/aliancadx-62000-install.apk';

function error(code, message, extra = {}) {
  return Object.assign(new Error(message), { code, ...extra });
}
function notify(fn, value) { if (typeof fn === 'function') fn(value); }
function diagnostic(step, cause, context = '', fallbackCode = 'OPERATION_FAILED') {
  const clean = value => String(value ?? '').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '').slice(0, 1600);
  const code = clean(cause?.code || (cause?.name !== 'Error' && cause?.name) || fallbackCode).slice(0, 80);
  const detail = clean(cause?.message || cause || 'O aparelho não confirmou a operação.');
  return {step, code, message: clean(context ? `${context} ${detail}` : detail)};
}
function timeout(promise, ms, onTimeout, code = 'TIMEOUT') {
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => { try { onTimeout?.(); } catch {} reject(error(code, 'O aparelho demorou a responder. Confira a tela do celular.')); }, ms);
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}
function abortError() { return error('CANCELLED', 'Operação USB cancelada. Confira no celular se a instalação foi concluída.'); }

// No IndexedDB/localStorage: each connection uses a new, temporary RSA identity.
export class SessionCredentialStore {
  constructor(webCrypto = globalThis.crypto) { this.crypto = webCrypto; this.keys = []; this.cleared = false; }
  async generateKey() {
    const pair = await this.crypto.subtle.generateKey({name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-1'}, true, ['sign', 'verify']);
    const key = {buffer: new Uint8Array(await this.crypto.subtle.exportKey('pkcs8', pair.privateKey)), name: 'AliancaDX-instalacao-temporaria'};
    if (this.cleared) { key.buffer.fill(0); throw abortError(); }
    this.keys.push(key);
    return key;
  }
  *iterateKeys() { yield* this.keys; }
  clear() { this.cleared = true; for (const key of this.keys) key.buffer.fill(0); this.keys = []; }
}

/**
 * Connect only after the person manually enabled USB debugging and selected
 * their own device. Android must separately accept this session's RSA key.
 */
export async function createClient({onStage, signal} = {}) {
  if (!globalThis.isSecureContext) throw error('HTTPS_REQUIRED', 'A instalação USB exige HTTPS.');
  if (!globalThis.navigator?.usb || !globalThis.crypto?.subtle) throw error('UNSUPPORTED', 'Este navegador não oferece os recursos USB necessários. Use um Chrome ou Edge compatível no computador.');
  if (signal?.aborted) throw abortError();
  const manager = new AdbDaemonWebUsbDeviceManager(globalThis.navigator.usb);
  // Keep the chooser inside the original user gesture, before any other await.
  const choice = manager.requestDevice();
  notify(onStage, {stage: 'chooser', message: 'Selecione seu Android com a depuração USB já ativada.'});
  const device = await choice;
  if (!device) throw error('NO_DEVICE', 'Nenhum aparelho foi selecionado. A depuração USB precisa estar ativa antes de o aparelho aparecer.');
  const credentials = new SessionCredentialStore();
  let connection;
  let adb;
  let aborted = false;
  const warn = (step, cause, context, code) => notify(onStage, {stage: 'warning', ...diagnostic(step, cause, context, code)});
  const stop = () => {
    aborted = true; credentials.clear();
    Promise.resolve().then(() => adb ? adb.close() : device.raw.close()).catch(cause => warn('abort-close', cause, 'Não foi possível confirmar o encerramento USB após o cancelamento.', 'CLOSE_FAILED'));
  };
  signal?.addEventListener('abort', stop, {once: true});
  try {
    if (signal?.aborted) throw abortError();
    notify(onStage, {stage: 'opening', message: 'Abrindo a conexão USB autorizada.'});
    connection = await timeout(device.connect(), 20000, stop);
    notify(onStage, {stage: 'authorize', message: 'Desbloqueie o celular e confirme a solicitação de depuração USB. Não marque autorização permanente.'});
    const transport = await timeout(AdbDaemonTransport.authenticate({serial: device.serial, connection, credentialStore: credentials}), 120000, stop, 'AUTH_TIMEOUT');
    if (aborted || signal?.aborted) {
      try { await timeout(transport.close(), 6000, undefined, 'CLOSE_TIMEOUT'); }
      catch (cause) { warn('authentication-close', cause, 'Não foi possível confirmar o encerramento da autenticação USB.', 'CLOSE_FAILED'); }
      throw abortError();
    }
    adb = new Adb(transport);
    if (!adb.subprocess.shellProtocol) throw error('SHELL_UNSUPPORTED', 'O aparelho não oferece a confirmação de comandos necessária para esta instalação.');
    notify(onStage, {stage: 'connected', message: 'Conexão ADB autorizada pelo celular.'});
    return makeAuthorizedClient(adb, {raw: device.raw, credentials, onStage, signal, abortListener: stop});
  } catch (cause) {
    signal?.removeEventListener('abort', stop);
    credentials.clear();
    try { await timeout(adb ? adb.close() : device.raw.close(), 6000, undefined, 'CLOSE_TIMEOUT'); }
    catch (cleanupCause) { warn('connection-cleanup', cleanupCause, 'Não foi possível confirmar o encerramento USB após o erro de conexão.', 'CLOSE_FAILED'); }
    throw cause;
  }
}

/** Exported for isolated tests; the web UI only receives createClient. */
export function makeAuthorizedClient(adb, {raw, credentials, onStage, signal, abortListener} = {}) {
  let closed = false;
  let transportClosed = false;
  let disconnected = false;
  let busy = false;
  let installedVerified = false;
  let modelCache;
  const stage = (name, message) => notify(onStage, {stage: name, message});
  const onDisconnected = () => {
    if (disconnected) return;
    disconnected = true;
    if (!closed) stage('disconnected', 'A conexão USB foi interrompida. Confira o aparelho; isto não confirma desligamento da depuração.');
  };
  adb.disconnected.then(onDisconnected, onDisconnected);
  const warn = (step, cause, context, code) => notify(onStage, {stage: 'warning', ...diagnostic(step, cause, context, code)});
  const closeAfterTimeout = () => { Promise.resolve().then(() => adb.close()).catch(cause => warn('timeout-close', cause, 'Não foi possível confirmar o encerramento USB após o tempo limite.', 'CLOSE_FAILED')); };
  const ensureOpen = () => {
    if (signal?.aborted) throw abortError();
    if (closed || disconnected) throw error('DISCONNECTED', 'A conexão USB foi encerrada.');
  };
  const run = async (args, ms = 20000) => {
    ensureOpen();
    // Callers below are fixed constants; there is deliberately no public shell API.
    return timeout(adb.subprocess.shellProtocol.spawnWaitText(args), ms, closeAfterTimeout);
  };
  const checked = async args => {
    const result = await run(args);
    if (result.exitCode !== 0) throw error('COMMAND_FAILED', String(result.stderr || result.stdout || 'O Android recusou a operação.').slice(0, 1600));
    return result.stdout.trim();
  };
  async function model() {
    ensureOpen();
    if (modelCache) return modelCache;
    const [manufacturer, modelName, sdk, abis64] = await Promise.all([
      adb.getProp('ro.product.manufacturer'), adb.getProp('ro.product.model'),
      adb.getProp('ro.build.version.sdk'), adb.getProp('ro.product.cpu.abilist64'),
    ].map(promise => timeout(promise, 15000, closeAfterTimeout)));
    const apiLevel = Number.parseInt(sdk, 10);
    const abis = abis64.split(',').map(value => value.trim()).filter(Boolean);
    modelCache = {manufacturer: String(manufacturer).slice(0, 120), model: String(modelName).slice(0, 120), apiLevel, abis, compatible: apiLevel >= 26 && abis.some(value => value === 'arm64-v8a' || value === 'x86_64')};
    return modelCache;
  }
  async function install(file, onProgress) {
    ensureOpen();
    if (busy) throw error('BUSY', 'Outra operação USB está em andamento.');
    busy = true;
    let sync;
    let pmSucceeded = false;
    try {
      const info = await model();
      if (!info.compatible) throw error('INCOMPATIBLE', 'Este APK requer Android 8 ou superior com sistema de 64 bits.');
      if (!file || typeof file.arrayBuffer !== 'function' || file.size !== APK_SIZE) throw error('APK_MISMATCH', 'Selecione o APK original AliançaDX 6.2.0 indicado nesta página.');
      const bytes = new Uint8Array(await file.arrayBuffer());
      const digest = new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', bytes));
      const hash = Array.from(digest, value => value.toString(16).padStart(2, '0')).join('');
      if (bytes.byteLength !== APK_SIZE || hash !== APK_SHA256) throw error('APK_MISMATCH', 'O hash do arquivo não corresponde ao APK AliançaDX 6.2.0 verificado. Nenhum arquivo foi instalado.');
      stage('upload', 'Enviando o APK verificado ao aparelho.');
      sync = await adb.sync();
      let offset = 0;
      notify(onProgress, {sent: 0, total: bytes.length, phase: 'upload'});
      const stream = new ReadableStream({
        pull(controller) {
          if (signal?.aborted) { controller.error(abortError()); return; }
          if (offset >= bytes.length) { controller.close(); return; }
          const end = Math.min(offset + 65536, bytes.length);
          controller.enqueue(bytes.subarray(offset, end));
          offset = end;
          notify(onProgress, {sent: offset, total: bytes.length, phase: 'upload'});
        },
      }, {highWaterMark: 0});
      await timeout(sync.write({filename: TEMP_APK, file: stream, permission: 0o644}), 180000, closeAfterTimeout, 'UPLOAD_TIMEOUT');
      await timeout(sync.dispose(), 6000, closeAfterTimeout, 'SYNC_CLOSE_TIMEOUT'); sync = null;
      notify(onProgress, {sent: bytes.length, total: bytes.length, phase: 'upload-complete'});
      stage('installing', 'O Android está verificando e instalando o APK. Confira se há uma confirmação na tela do celular.');
      const result = await run(['pm', 'install', '-r', TEMP_APK], 120000);
      if (result.exitCode !== 0 || !/^Success\s*$/m.test(result.stdout)) throw error('INSTALL_FAILED', String(result.stderr || result.stdout || 'O Android não confirmou a instalação.').slice(0, 1600));
      pmSucceeded = true;
      stage('verifying', 'Conferindo o pacote e a versão instalados.');
      const packagePath = await checked(['pm', 'path', APK_PACKAGE]);
      if (!/^package:\//m.test(packagePath)) throw error('VERIFY_FAILED', 'O gerenciador não confirmou o pacote AliançaDX instalado.');
      const details = await checked(['dumpsys', 'package', APK_PACKAGE]);
      const match = /\bversionCode=(\d+)/.exec(details);
      if (!match || Number(match[1]) !== APK_VERSION_CODE) throw error('VERIFY_FAILED', 'A versão instalada não pôde ser confirmada como AliançaDX 6.2.0.');
      installedVerified = true;
      stage('installed', 'AliançaDX 6.2.0 instalado e confirmado pelo Android.');
      return {installed: true, packageName: APK_PACKAGE, versionCode: APK_VERSION_CODE, verified: true};
    } catch (cause) {
      if (pmSucceeded || ['TIMEOUT', 'UPLOAD_TIMEOUT'].includes(cause.code)) cause.installationMayHaveCompleted = pmSucceeded || cause.code === 'TIMEOUT';
      throw cause;
    } finally {
      if (sync) {
        try { await timeout(sync.dispose(), 6000, closeAfterTimeout, 'SYNC_CLOSE_TIMEOUT'); }
        catch (cause) { warn('sync-close', cause, 'Não foi possível confirmar o encerramento do envio do APK.', 'SYNC_CLOSE_FAILED'); }
      }
      // Only our fixed temporary APK is removed. Installed app/data are untouched.
      if (!closed && !disconnected) {
        try { await timeout(adb.rm(TEMP_APK, {force: true}), 6000, closeAfterTimeout, 'TEMP_CLEANUP_TIMEOUT'); }
        catch (cause) { warn('temporary-apk-remove', cause, 'Não foi possível confirmar a remoção do APK temporário no aparelho. Confira o resultado no celular e encerre a depuração.', 'TEMP_CLEANUP_FAILED'); }
      }
      busy = false;
    }
  }
  async function cleanupDeveloperOptions() {
    ensureOpen();
    if (!installedVerified) throw error('INSTALL_NOT_VERIFIED', 'Finalize a instalação verificada antes de solicitar o encerramento da depuração.');
    if (busy) throw error('BUSY', 'Outra operação USB está em andamento.');
    busy = true;
    const result = {developerOptions: 'unconfirmed', usbDebugging: 'unconfirmed', disconnected: false, manualVerificationRequired: true, errors: []};
    const record = (step, cause, context, code) => {
      result.errors.push(diagnostic(step, cause, context, code));
      result.error = result.errors.map(entry => entry.message).join('\n');
    };
    try {
      stage('cleanup', 'Solicitando o encerramento das opções de desenvolvedor. A conexão USB pode ser encerrada antes da confirmação.');
      let devDisabled = false;
      try {
        await checked(['settings', 'put', 'global', 'development_settings_enabled', '0']);
        devDisabled = true;
      } catch (cause) {
        record('developer-options-disable', cause, 'O Android não confirmou o pedido para desligar as opções de desenvolvedor.');
      }
      if (devDisabled) {
        try {
          const devValue = await checked(['settings', 'get', 'global', 'development_settings_enabled']);
          if (devValue === '0') result.developerOptions = 'confirmed-off';
          else record('developer-options-readback', error('VERIFY_UNCONFIRMED', `Valor retornado pelo Android: ${devValue || '(vazio)'}.`), 'As opções de desenvolvedor ainda não foram confirmadas como desligadas.');
        } catch (cause) {
          record('developer-options-readback', cause, 'Não foi possível ler a confirmação de desligamento das opções de desenvolvedor.');
        }
      }
      // Disabling ADB is deliberately LAST: it can immediately destroy this link.
      ensureOpen();
      result.usbDebugging = 'attempted';
      const off = await run(['settings', 'put', 'global', 'adb_enabled', '0'], 10000);
      if (off.exitCode !== 0) {
        result.usbDebugging = 'unconfirmed';
        record('usb-debugging-disable', error('COMMAND_FAILED', off.stderr || off.stdout || `O comando retornou código ${off.exitCode}.`), 'O Android recusou ou não confirmou o pedido para desligar a depuração USB.');
      }
      else {
        // If the OEM still allows one read, use it; otherwise keep 'attempted'.
        try {
          const value = await checked(['settings', 'get', 'global', 'adb_enabled']);
          if (value === '0') result.usbDebugging = 'confirmed-off';
          else record('usb-debugging-readback', error('VERIFY_UNCONFIRMED', `Valor retornado pelo Android: ${value || '(vazio)'}.`), 'A depuração USB ainda não foi confirmada como desligada.');
        } catch (cause) {
          record('usb-debugging-readback', cause, 'Não foi possível ler a confirmação de desligamento da depuração USB. A conexão pode ter sido encerrada.');
        }
      }
    } catch (cause) {
      record('usb-debugging-disable', cause, 'Não foi possível confirmar o pedido para desligar a depuração USB.');
    } finally {
      result.disconnected = disconnected;
      busy = false;
    }
    return result;
  }
  async function close({forget = false} = {}) {
    closed = true;
    const errors = [];
    if (!transportClosed) {
      try { await timeout(adb.close(), 6000, undefined, 'CLOSE_TIMEOUT'); transportClosed = true; }
      catch (cause) { errors.push(diagnostic('usb-close', cause, 'Não foi possível confirmar o encerramento da conexão USB. Desconecte o cabo e confira as opções no celular.', 'CLOSE_FAILED')); }
    }
    signal?.removeEventListener('abort', abortListener);
    credentials?.clear();
    let forgotten = false;
    if (forget && typeof raw?.forget === 'function') {
      try { await timeout(raw.forget(), 6000, undefined, 'FORGET_TIMEOUT'); forgotten = true; }
      catch (cause) { errors.push(diagnostic('webusb-forget', cause, 'Não foi possível confirmar a revogação da autorização WebUSB. Remova-a nas permissões deste site no navegador.', 'FORGET_FAILED')); }
    } else if (forget) {
      errors.push(diagnostic('webusb-forget', error('FORGET_UNSUPPORTED', 'O navegador não oferece a função de esquecer este dispositivo.'), 'Remova a autorização WebUSB nas permissões deste site.'));
    }
    return {closed: raw ? raw.opened === false : transportClosed, forgotten, errors};
  }
  return {model, install, cleanupDeveloperOptions, close};
}
