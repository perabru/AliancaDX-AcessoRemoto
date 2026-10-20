#!/usr/bin/env node
'use strict';
/*
 * Testes de contrato da UI, sem navegador, aparelho, APK real ou servidor.
 * A fixture abaixo tem o tamanho oficial, mas contém bytes sintéticos.
 * O SHA-256 aprovado é devolvido por um mock explícito de WebCrypto para testar
 * o fluxo e o snapshot; estes testes NÃO validam a assinatura nem a segurança
 * de um APK distribuído. A conferência do APK real é uma verificação separada.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const assistant = require('../assets/usb-install-assistant.js');
const { APK } = assistant;
const SYNTHETIC_BYTES = new Uint8Array(APK.size);
SYNTHETIC_BYTES[0] = 0x41;
const GOOD_DIGEST = Uint8Array.from(Buffer.from(APK.sha256, 'hex')).buffer;
const BAD_DIGEST = new Uint8Array(32).buffer;
const GOOD_RESULT = { installed: true, verified: true, packageName: APK.packageName, versionCode: APK.versionCode };
const turn = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function fixture(overrides) {
  const file = { name: 'fixture-sintetica-nao-e-um-apk.apk', size: APK.size, reads: 0, async arrayBuffer() { this.reads += 1; return SYNTHETIC_BYTES.buffer; } };
  return Object.assign(file, overrides || {});
}
function cryptoMock(digest) { return { subtle: { async digest(algorithm, bytes) { assert.equal(algorithm, 'SHA-256'); assert.equal(bytes.byteLength, APK.size); return digest || GOOD_DIGEST; } } }; }
function fakeTimers() {
  const jobs = new Map(); let id = 0;
  return { jobs, setTimeout(fn, ms) { const key = ++id; jobs.set(key, { fn, ms }); return key; }, clearTimeout(key) { jobs.delete(key); }, fire(ms) { for (const [key, job] of Array.from(jobs)) if (job.ms === ms) { jobs.delete(key); job.fn(); } } };
}
function harness(options) {
  options = options || {};
  const calls = { create: 0, model: 0, install: 0, cleanup: 0, close: 0, closeArgs: [], stage: null, signal: null, progress: null, installedFile: null };
  const events = [], states = [], timers = fakeTimers(); let tick = 0;
  const client = {
    async model() { calls.model += 1; return options.model || { manufacturer: 'Fabricante de teste', model: 'Modelo sintético', apiLevel: 33, abis: ['arm64-v8a'], compatible: true }; },
    install(file, onProgress) { calls.install += 1; calls.installedFile = file; calls.progress = onProgress; if (options.install) return options.install(file, onProgress, calls); return Promise.resolve(GOOD_RESULT); },
    async cleanupDeveloperOptions() { calls.cleanup += 1; if (options.cleanupError) throw options.cleanupError; return options.cleanupResult || { developerOptions: 'unconfirmed', usbDebugging: 'attempted', disconnected: true, manualVerificationRequired: true }; },
    async close(args) { calls.close += 1; calls.closeArgs.push(args); if (options.close) return options.close(args, calls); if (options.closeError) throw options.closeError; return options.closeResult || { closed: true, forgotten: true, errors: [] }; }
  };
  const sdk = { createClient({ onStage, signal }) { calls.create += 1; calls.stage = onStage; calls.signal = signal; if (options.create) return options.create({ onStage, signal, client }); return Promise.resolve(client); } };
  const controller = assistant.createController({
    crypto: options.crypto || cryptoMock(),
    loadSdk: options.loadSdk || (async () => sdk),
    timers,
    AbortController,
    now: () => new Date(Date.UTC(2026, 9, 20, 12, 0, tick++)),
    onEvent: item => events.push(item),
    onState: state => states.push(state)
  });
  return { controller, calls, client, sdk, events, states, timers };
}
async function ready(h) { assert.equal(await h.controller.prepare(), true); const result = await h.controller.selectFile(fixture()); assert.equal(result.valid, true); }
async function connected(h) { await ready(h); assert.equal(await h.controller.connect(true), true); }
function history(h) { return h.events.map(entry => entry.message).join('\n'); }

// Validações rápidas devem acontecer antes de ler qualquer arquivo ou abrir USB.
test('recusa arquivo ausente, tamanho incorreto, tamanho lido divergente e ausência de WebCrypto', async () => {
  await assert.rejects(assistant.validateApk(null, cryptoMock()), { code: 'NO_APK' });
  const small = fixture({ size: 3 });
  await assert.rejects(assistant.validateApk(small, cryptoMock()), { code: 'APK_SIZE' });
  assert.equal(small.reads, 0);
  await assert.rejects(assistant.validateApk(fixture(), null), { code: 'CRYPTO_UNAVAILABLE' });
  await assert.rejects(assistant.validateApk(fixture({ arrayBuffer: async () => new ArrayBuffer(3) }), cryptoMock()), { code: 'APK_SIZE' });
});

test('recusa SHA divergente e conserva cópia imutável dos mesmos bytes validados', async () => {
  await assert.rejects(assistant.validateApk(fixture(), cryptoMock(BAD_DIGEST)), { code: 'APK_HASH' });
  const input = fixture(); const original = SYNTHETIC_BYTES[0];
  const valid = await assistant.validateApk(input, cryptoMock());
  assert.equal(valid.sha256, APK.sha256);
  assert.equal(valid.size, APK.size);
  assert.equal(valid.packageName, APK.packageName);
  assert.equal(valid.versionCode, APK.versionCode);
  assert.equal(input.reads, 1);
  assert.notEqual(valid.file, input);
  SYNTHETIC_BYTES[0] = 0x5a;
  assert.equal(new Uint8Array(await valid.file.slice(0, 1).arrayBuffer())[0], original, 'o Blob precisa preservar os bytes do momento da validação');
  SYNTHETIC_BYTES[0] = original;
});

test('não solicita USB sem consentimento, sem arquivo aprovado ou após hash recusado', async () => {
  const h = harness(); await h.controller.prepare();
  assert.equal(await h.controller.connect(false), false);
  assert.equal(h.controller.getState().error.code, 'CONSENT_REQUIRED');
  assert.equal(await h.controller.connect(true), false);
  assert.equal(h.controller.getState().error.code, 'NO_APK');
  assert.equal(h.calls.create, 0);
  const bad = harness({ crypto: cryptoMock(BAD_DIGEST) }); await bad.controller.prepare();
  assert.equal(await bad.controller.selectFile(fixture()), null);
  assert.equal(await bad.controller.connect(true), false);
  assert.equal(bad.calls.create, 0);
  await h.controller.selectFile(fixture());
  assert.equal(await h.controller.connect(false), false);
  assert.equal(h.calls.create, 0);
});

test('conectar só consulta compatibilidade: nunca instala ou desliga opções automaticamente', async () => {
  const h = harness(); await connected(h);
  assert.equal(h.calls.create, 1); assert.equal(h.calls.model, 1);
  assert.equal(h.calls.install, 0); assert.equal(h.calls.cleanup, 0);
  assert.equal(h.controller.getState().connected, true);
  assert.equal(h.controller.getState().installVerified, false);
  assert.equal(h.timers.jobs.size, 0, 'watchdogs da conexão precisam ser removidos');
  assert.equal(await h.controller.cleanup(), null);
  assert.equal(h.calls.cleanup, 0, 'não desligar opções antes da instalação confirmada');
  await h.controller.disconnect();
  assert.deepEqual(h.calls.closeArgs, [{ forget: true }]);
});

test('recusa aparelho incompatível antes de instalar e fecha a conexão', async () => {
  const h = harness({ model: { manufacturer: 'Teste', model: '32 bits', apiLevel: 25, abis: ['armeabi-v7a'], compatible: false } });
  await ready(h); assert.equal(await h.controller.connect(true), false);
  assert.equal(h.controller.getState().error.code, 'INCOMPATIBLE');
  assert.equal(h.calls.install, 0); assert.equal(h.calls.cleanup, 0); assert.equal(h.calls.close, 1);
});

test('100% dos bytes não confirma instalação; só pacote e versão verificados liberam o encerramento', async () => {
  const pending = deferred(); const h = harness({ install: (_file, onProgress) => { onProgress({ sent: APK.size, total: APK.size, phase: 'upload' }); return pending.promise; } });
  await connected(h); const fileSnapshot = h.controller.getState().fileInfo.file;
  const operation = h.controller.install({ cleanup: true });
  const state = h.controller.getState();
  assert.equal(state.progress.sent, APK.size);
  assert.equal(state.stage, 'uploading');
  assert.equal(state.installVerified, false); assert.equal(h.calls.cleanup, 0);
  assert.equal(h.calls.installedFile, fileSnapshot, 'instalar o Blob aprovado, não reler o arquivo externo');
  h.calls.stage({ stage: 'installing', message: 'Instalador Android em andamento, sem porcentagem.' });
  assert.equal(h.controller.getState().stage, 'installing');
  assert.equal(h.controller.getState().installVerified, false);
  pending.resolve(GOOD_RESULT); assert.equal(await operation, true);
  assert.equal(h.calls.cleanup, 1); assert.equal(h.calls.close, 1);
  assert.equal(h.controller.getState().installVerified, true);
  assert.equal(h.controller.getState().manualChecked, false);
  assert.equal(h.controller.getState().cleanup.developerOptions, 'unconfirmed');
  assert.match(history(h), /não foi confirmado|precisa ser conferido manualmente/);
  h.controller.confirmManual(true);
  assert.equal(h.controller.getState().manualChecked, true);
  assert.match(history(h), /Esta confirmação é manual/);
  for (const event of h.events) assert.ok(Number.isFinite(Date.parse(event.time)), 'cada evento deve conter horário ISO válido');
});

test('resultado com pacote ou versão divergentes não confirma instalação e não chama cleanup', async () => {
  for (const result of [{ ...GOOD_RESULT, packageName: 'com.outro.aplicativo' }, { ...GOOD_RESULT, versionCode: 61999 }, { ...GOOD_RESULT, verified: false }]) {
    const h = harness({ install: async () => result }); await connected(h);
    assert.equal(await h.controller.install({ cleanup: true }), false);
    assert.equal(h.controller.getState().installVerified, false);
    assert.equal(h.controller.getState().error.code, 'VERIFICATION_FAILED');
    assert.equal(h.calls.cleanup, 0); assert.equal(h.calls.close, 1);
    h.controller.confirmManual(true); assert.equal(h.controller.getState().manualChecked, false);
  }
});

test('falha de instalação por assinatura recebe orientação própria e não executa cleanup', async () => {
  const failure = Object.assign(new Error('Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]'), { code: 'INSTALL_FAILED' });
  const h = harness({ install: async () => { throw failure; } }); await connected(h);
  assert.equal(await h.controller.install({ cleanup: true }), false);
  const state = h.controller.getState();
  assert.match(state.error.title, /assinatura/i);
  assert.doesNotMatch(state.error.title, /requisitos/i);
  assert.match(state.error.action, /Não desinstale/i);
  assert.equal(h.calls.cleanup, 0); assert.equal(h.calls.close, 1);
  assert.equal(state.installVerified, false);
});

test('erros Android distintos oferecem ações próprias sem recomendar desativar proteções', () => {
  const cases = [
    ['INSTALL_FAILED', 'INSTALL_FAILED_VERSION_DOWNGRADE', /versão mais recente/i],
    ['INSTALL_FAILED', 'INSTALL_FAILED_INSUFFICIENT_STORAGE', /espaço/i],
    ['INSTALL_FAILED', 'INSTALL_FAILED_USER_RESTRICTED', /bloqueou/i],
    ['INCOMPATIBLE', 'Android incompatível', /requisitos/i],
    ['VERIFY_FAILED', 'Pacote não confirmado', /não pôde ser confirmada/i],
    ['NotFoundError', 'Seleção cancelada', /Nenhum aparelho/i],
    ['NotAllowedError', 'USB negado', /não autorizou/i]
  ];
  for (const [code, message, title] of cases) assert.match(assistant.formatError({ code, message }).title, title);
  assert.match(assistant.formatError({ code: 'INSTALL_FAILED', message: 'INSTALL_FAILED_USER_RESTRICTED' }).action, /não desativa o Play Protect/i);
});

test('cancelamento aborta a autorização, ignora callbacks atrasados e fecha cliente entregue depois', async () => {
  const pending = deferred(); const h = harness({ create: () => pending.promise }); await ready(h);
  const operation = h.controller.connect(true); assert.equal(h.controller.getState().busy, true);
  await h.controller.cancel(); assert.equal(await operation, false);
  assert.equal(h.calls.signal.aborted, true);
  const before = h.events.length; const stateBefore = h.controller.getState().stage;
  h.calls.stage({ stage: 'authorizing', message: 'CALLBACK ATRASADO NÃO DEVE APARECER' });
  assert.equal(h.events.length, before, 'callbacks da sessão cancelada devem ser ignorados');
  assert.equal(h.controller.getState().stage, stateBefore);
  pending.resolve(h.client); await turn();
  assert.equal(h.calls.close, 1, 'cliente que chega após cancelamento deve ser fechado');
  assert.deepEqual(h.calls.closeArgs, [{ forget: true }]);
  assert.equal(h.calls.model, 0); assert.equal(h.calls.install, 0); assert.equal(h.calls.cleanup, 0);
});

test('cancelar durante transferência não confirma resultado nem aceita progresso tardio', async () => {
  const pending = deferred(); const h = harness({ install: () => pending.promise }); await connected(h);
  const operation = h.controller.install({ cleanup: true });
  h.calls.progress({ sent: 100, total: APK.size, phase: 'upload' });
  await h.controller.cancel(); assert.equal(await operation, false);
  assert.equal(h.calls.signal.aborted, true);
  const before = h.events.length; const stateBefore = h.controller.getState();
  h.calls.progress({ sent: APK.size, total: APK.size, phase: 'upload' });
  h.calls.stage({ stage: 'installing', message: 'ESTÁGIO ATRASADO NÃO DEVE APARECER' });
  assert.equal(h.events.length, before, 'progresso e estágios cancelados devem ser ignorados');
  assert.equal(h.controller.getState().message, stateBefore.message);
  assert.equal(h.controller.getState().installVerified, false);
  assert.equal(h.calls.cleanup, 0);
  pending.resolve(GOOD_RESULT); await turn();
  assert.equal(h.controller.getState().installVerified, false, 'resultado tardio não pode transformar cancelamento em sucesso');
});

test('watchdog informa espera em 30 s e aborta conexão em 60 s sem relógio real', async () => {
  const pending = deferred(); const h = harness({ create: () => pending.promise }); await ready(h);
  const operation = h.controller.connect(true);
  h.timers.fire(30000); assert.match(history(h), /seleção USB ainda não foi concluída/);
  assert.match(history(h), /cancele e copie o diagnóstico/);
  assert.equal(h.calls.signal.aborted, false);
  h.timers.fire(60000); assert.equal(await operation, false);
  assert.equal(h.calls.signal.aborted, true);
  assert.equal(h.controller.getState().error.code, 'CONNECT_TIMEOUT');
  assert.equal(h.controller.getState().installVerified, false);
  assert.equal(h.calls.cleanup, 0); assert.equal(h.calls.install, 0);
  assert.equal(h.timers.jobs.size, 0);
  pending.resolve(h.client); await turn(); assert.equal(h.calls.close, 1);
});

test('falhas de cleanup e close aparecem no histórico sem apagar uma instalação realmente verificada', async () => {
  const h = harness({ cleanupError: new Error('FALHA_CLEANUP_TESTE'), closeResult: { closed: false, forgotten: false, errors: ['FALHA_CLOSE_TESTE', { step: 'forget', message: 'FALHA_FORGET_TESTE' }] } });
  await connected(h); assert.equal(await h.controller.install({ cleanup: true }), true);
  assert.equal(h.controller.getState().installVerified, true);
  assert.equal(h.controller.getState().manualChecked, false);
  assert.equal(h.controller.getState().cleanup.developerOptions, 'unconfirmed');
  assert.equal(h.controller.getState().closeResult.closed, false);
  assert.match(history(h), /FALHA_CLEANUP_TESTE/);
  assert.match(history(h), /FALHA_CLOSE_TESTE/);
  assert.match(history(h), /FALHA_FORGET_TESTE/);
});

test('erros retornados pelo cleanup também são apresentados com horário e não viram confirmação de desligamento', async () => {
  const h = harness({ cleanupResult: { developerOptions: 'unconfirmed', usbDebugging: 'attempted', disconnected: true, manualVerificationRequired: true, errors: ['FALHA_RETURN_CLEANUP_TESTE', { step: 'settings-read', message: 'FALHA_READBACK_TESTE' }] } });
  await connected(h); assert.equal(await h.controller.install({ cleanup: true }), true);
  assert.equal(h.controller.getState().cleanup.usbDebugging, 'attempted');
  assert.equal(h.controller.getState().manualChecked, false);
  assert.match(history(h), /FALHA_RETURN_CLEANUP_TESTE/);
  assert.match(history(h), /FALHA_READBACK_TESTE/);
});

test('SDK indisponível não solicita USB e preserva diagnóstico de falha', async () => {
  const h = harness({ loadSdk: async () => { throw new Error('BUNDLE_INDISPONIVEL_TESTE'); } });
  assert.equal(await h.controller.prepare(), false);
  assert.equal(h.controller.getState().sdkReady, false);
  assert.equal(h.controller.getState().error.code, 'SDK_LOAD');
  assert.match(h.controller.getState().error.detail, /BUNDLE_INDISPONIVEL_TESTE/);
  await h.controller.selectFile(fixture());
  assert.equal(await h.controller.connect(true), false);
  assert.equal(h.calls.create, 0); assert.equal(h.calls.install, 0);
  assert.equal(h.controller.getState().error.code, 'SDK_LOAD');
});

test('terminar carga do SDK não apaga erro anterior de arquivo inválido', async () => {
  const pending = deferred(); const h = harness({ loadSdk: () => pending.promise });
  const loading = h.controller.prepare();
  await h.controller.selectFile(fixture({ size: 1 }));
  assert.equal(h.controller.getState().error.code, 'APK_SIZE');
  pending.resolve(h.sdk); assert.equal(await loading, true);
  assert.equal(h.controller.getState().error.code, 'APK_SIZE');
  assert.equal(h.controller.getState().fileInfo, null);
});

test('encerramento pendente mantém busy e impede conexão concorrente até fechar de verdade', async () => {
  const closing = deferred(); const h = harness({ close: () => closing.promise }); await connected(h);
  const operation = h.controller.disconnect();
  await turn();
  assert.equal(h.controller.getState().busy, true);
  assert.equal(h.calls.close, 1);
  assert.equal(await h.controller.connect(true), false);
  assert.equal(h.calls.create, 1, 'não abrir outro cliente enquanto o primeiro fecha');
  assert.equal(h.controller.getState().busy, true);
  closing.resolve({ closed: true, forgotten: true, errors: [] }); await operation;
  assert.equal(h.controller.getState().busy, false);
  assert.equal(h.controller.getState().connected, false);
  assert.equal(await h.controller.connect(true), true);
  assert.equal(h.calls.create, 2);
  assert.equal(h.controller.getState().connected, true);
  await h.controller.disconnect();
});

test('cancelamento e finalizador de install compartilham close pendente e não sobrescrevem nova sessão', async () => {
  const installing = deferred(), closing = deferred();
  const h = harness({ install: () => installing.promise, close: () => closing.promise }); await connected(h);
  const oldStage = h.calls.stage;
  const installOperation = h.controller.install({ cleanup: true });
  const cancelOperation = h.controller.cancel();
  let installFinished = false; installOperation.then(() => { installFinished = true; });
  await turn();
  assert.equal(h.controller.getState().busy, true);
  assert.equal(installFinished, false, 'a instalação não encerra a UI enquanto o close ainda está pendente');
  assert.equal(h.calls.close, 1, 'cancel e finally devem compartilhar a mesma tentativa de fechamento');
  assert.equal(await h.controller.connect(true), false);
  assert.equal(h.calls.create, 1);
  closing.resolve({ closed: true, forgotten: true, errors: [] });
  await cancelOperation; assert.equal(await installOperation, false);
  assert.equal(h.controller.getState().busy, false);
  assert.equal(h.calls.close, 1);
  assert.equal(await h.controller.connect(true), true);
  assert.equal(h.controller.getState().connected, true);
  const before = h.events.length;
  oldStage({ stage: 'authorizing', message: 'SESSAO ANTIGA NAO PODE SOBRESCREVER A NOVA' });
  installing.resolve(GOOD_RESULT); await turn();
  assert.equal(h.events.length, before);
  assert.equal(h.controller.getState().connected, true);
  assert.equal(h.controller.getState().stage, 'connected');
  assert.equal(h.controller.getState().installVerified, false);
  assert.equal(h.calls.cleanup, 0);
  await h.controller.disconnect();
});

test('nova conexão e nova seleção de arquivo limpam aprovação e conferência da sessão anterior', async () => {
  const h = harness(); await connected(h);
  assert.equal(await h.controller.install({ cleanup: false }), true);
  h.controller.confirmManual(true);
  assert.equal(h.controller.getState().installVerified, true);
  assert.equal(h.controller.getState().manualChecked, true);
  assert.ok(h.controller.getState().device);
  const connecting = h.controller.connect(true);
  const pendingState = h.controller.getState();
  assert.equal(pendingState.installVerified, false);
  assert.equal(pendingState.manualChecked, false);
  assert.equal(pendingState.device, null);
  assert.equal(await connecting, true);
  await h.controller.disconnect();
  await h.controller.selectFile(fixture());
  const newFileState = h.controller.getState();
  assert.equal(newFileState.installVerified, false);
  assert.equal(newFileState.manualChecked, false);
  assert.equal(newFileState.device, null);
  assert.equal(newFileState.cleanup, null);
});

test('evento de cabo desconectado invalida conexão e só permite reconectar após o fechamento pendente', async () => {
  const closing = deferred(); const h = harness({ close: () => closing.promise }); await connected(h);
  h.calls.stage({ stage: 'disconnected', code: 'DISCONNECTED', message: 'O cabo do aparelho consultado foi removido.' });
  assert.equal(h.controller.getState().connected, false);
  assert.equal(h.controller.getState().busy, true);
  assert.equal(h.controller.getState().error.code, 'DISCONNECTED');
  assert.equal(h.calls.close, 1);
  assert.equal(await h.controller.connect(true), false);
  assert.equal(h.calls.create, 1);
  closing.resolve({ closed: true, forgotten: true, errors: [] }); await turn();
  assert.equal(h.controller.getState().busy, false);
  assert.equal(h.controller.getState().connected, false);
  assert.equal(await h.controller.install({ cleanup: true }), false);
  assert.equal(h.calls.install, 0); assert.equal(h.calls.cleanup, 0);
  assert.equal(await h.controller.connect(true), true);
  assert.equal(h.controller.getState().connected, true);
  await h.controller.disconnect();
});

test('hash curto na tela nunca altera os 64 caracteres enviados ao clipboard', async () => {
  const received = []; const controls = { button: { disabled: false }, full: { open: false }, status: { textContent: '' } };
  assert.equal(await assistant.copyHash(controls, { navigator: { clipboard: { writeText: async text => received.push(text) } } }), true);
  assert.deepEqual(received, [APK.sha256]);
  assert.match(received[0], /^[a-f0-9]{64}$/);
  assert.equal(controls.full.open, false);
  assert.match(controls.status.textContent, /64 caracteres/);
  assert.equal(controls.button.disabled, false);
});

test('falha do clipboard abre hash completo sem anunciar cópia concluída', async () => {
  for (const navigator of [{}, { clipboard: { writeText: async () => { throw new Error('Clipboard denied'); } } }]) {
    const controls = { button: { disabled: false }, full: { open: false }, status: { textContent: 'Status anterior' } };
    assert.equal(await assistant.copyHash(controls, { navigator }), false);
    assert.equal(controls.full.open, true);
    assert.match(controls.status.textContent, /Não foi possível copiar/);
    assert.doesNotMatch(controls.status.textContent, /completo copiado/);
    assert.equal(controls.button.disabled, false);
  }
  assert.equal(await assistant.copyHash({}, {}), false, 'marcadores opcionais ausentes não podem quebrar a página');
});

test('dois grupos de hash são inicializados fora do assistente e não recebem handlers duplicados', async () => {
  const copied = [];
  function group() {
    const button = { hidden: true, handlers: [], addEventListener(type, handler) { assert.equal(type, 'click'); this.handlers.push(handler); } };
    const full = { open: false }, status = { textContent: '' };
    return { dataset: {}, button, full, status, querySelector(selector) { return { '[data-aadx-hash-copy]': button, '[data-aadx-hash-full]': full, '[data-aadx-hash-status]': status }[selector] || null; } };
  }
  const groups = [group(), group()];
  const document = { querySelectorAll(selector) { assert.equal(selector, '[data-aadx-hash]'); return groups; }, getElementById() { return null; } };
  const env = { navigator: { clipboard: { writeText: async text => copied.push(text) } } };
  assistant.init(document, env); assistant.init(document, env);
  for (const item of groups) { assert.equal(item.button.hidden, false); assert.equal(item.button.handlers.length, 1); await item.button.handlers[0](); }
  assert.deepEqual(copied, [APK.sha256, APK.sha256]);
  assistant.bindHashControls({ querySelectorAll: () => [] }, {});
  assistant.bindHashControls(null, {});
});

test('diagnóstico inclui etapa, modelo, API, ABI, erro e horários sem serial, chaves, PIN ou arquivo', () => {
  const state = {
    stage: 'error', message: 'Falha na etapa de instalação.', busy: false, connected: true, installVerified: false, manualChecked: false,
    fileInfo: { valid: true, name: 'NOME_PRIVADO_DO_ARQUIVO.apk', file: { privateKey: 'CHAVE_PRIVADA_DENTRO_DO_BLOB' } },
    device: { manufacturer: 'Xiaomi', model: 'POCO X6', apiLevel: 33, abis: ['arm64-v8a'], serialNumber: 'SERIAL_NAO_PUBLICAR' },
    error: { code: 'USB_BUSY', title: 'Interface ocupada', detail: 'processo recusou; PIN=913741; serialNumber=SERIAL_NO_DETALHE; password=SENHA_NAO_PUBLICAR; token=TOKEN_NAO_PUBLICAR', action: 'Confira a conexão.', stack: 'STACK_NAO_PUBLICAR' },
    logs: [{ time: '2026-10-20T12:00:01.000Z', level: 'error', message: 'privateKey=CHAVE_NAO_PUBLICAR' }, { time: '2026-10-20T12:00:02.000Z', level: 'warning', message: 'PIN 823765; deviceSerial=OUTRO_SERIAL_PRIVADO; depuração pendente.' }],
    privateKey: 'CHAVE_FORA_DA_LISTA_PERMITIDA', pin: 'PIN_FORA_DA_LISTA_PERMITIDA'
  };
  const text = assistant.buildDiagnostic(state, new Date('2026-10-20T12:10:00.000Z'));
  for (const expected of ['2026-10-20T12:10:00.000Z', 'Etapa: error', 'Xiaomi', 'POCO X6', 'Android API: 33', 'arm64-v8a', 'USB_BUSY', '2026-10-20T12:00:01.000Z', 'Instalação confirmada: não']) assert.ok(text.includes(expected), expected);
  for (const secret of ['NOME_PRIVADO_DO_ARQUIVO', 'CHAVE_PRIVADA_DENTRO_DO_BLOB', 'SERIAL_NAO_PUBLICAR', '913741', 'SERIAL_NO_DETALHE', 'SENHA_NAO_PUBLICAR', 'TOKEN_NAO_PUBLICAR', 'STACK_NAO_PUBLICAR', 'CHAVE_NAO_PUBLICAR', '823765', 'OUTRO_SERIAL_PRIVADO', 'CHAVE_FORA_DA_LISTA_PERMITIDA', 'PIN_FORA_DA_LISTA_PERMITIDA']) assert.ok(!text.includes(secret), 'não deve incluir ' + secret);
  assert.match(text, /dado omitido|chave omitida|PIN omitido/);
  assert.match(assistant.buildDiagnostic(), /Nenhuma etapa registrada/);
});

test('copiar diagnóstico mantém estado da operação e disponibiliza fallback selecionável quando necessário', async () => {
  const state = { stage: 'uploading', message: 'Transferência em andamento', busy: true, connected: true, installVerified: false, device: { manufacturer: 'Teste', model: 'Aparelho', apiLevel: 33, abis: ['arm64-v8a'] }, logs: [] };
  let clipboardText = '';
  const controls = { button: { disabled: false }, status: { textContent: '' }, fallback: { hidden: false, value: '' } };
  assert.equal(await assistant.copyDiagnostic(state, controls, { navigator: { clipboard: { writeText: async text => { clipboardText = text; } } } }), true);
  assert.match(clipboardText, /Operação em andamento: sim/);
  assert.match(controls.status.textContent, /Diagnóstico copiado/);
  assert.equal(controls.fallback.hidden, true);
  assert.equal(state.busy, true); assert.equal(state.stage, 'uploading'); assert.equal(state.installVerified, false);
  let focused = false, selected = false;
  const fallback = { hidden: true, value: '', focus() { focused = true; }, select() { selected = true; } };
  const denied = { button: { disabled: false }, status: { textContent: '' }, fallback };
  assert.equal(await assistant.copyDiagnostic(state, denied, { navigator: {} }), false);
  assert.equal(fallback.hidden, false); assert.equal(focused, true); assert.equal(selected, true);
  assert.match(fallback.value, /Etapa: uploading/);
  assert.match(denied.status.textContent, /Não foi possível copiar/);
  assert.doesNotMatch(denied.status.textContent, /Diagnóstico copiado/);
  assert.equal(denied.button.disabled, false);
  assert.equal(await assistant.copyDiagnostic(state, {}, {}), false);
});

test('ocupação USB e recusa de interface não são confundidas com HTTPS', () => {
  for (const error of [
    { code: 'USB_BUSY', message: 'Interface busy' },
    { name: 'DeviceBusyError', message: 'Device already in use by another program' },
    { name: 'NetworkError', message: 'The device is already in used by another program' },
    { name: 'NetworkError', message: 'The interface is already being used' }
  ]) assert.match(assistant.formatError(error).title, /Outro programa ou aba/);
  const claim = assistant.formatError({ name: 'SecurityError', message: 'Unable to claimInterface: access denied' });
  assert.match(claim.title, /interface USB/);
  assert.doesNotMatch(claim.title, /HTTPS/);
  assert.match(assistant.formatError({ name: 'SecurityError', message: 'The page is not in a secure context; HTTPS is required' }).title, /HTTPS/);
  assert.match(assistant.formatError({ code: 18, name: 'SecurityError', message: 'The page is not in a secure context; HTTPS is required' }).title, /HTTPS/);
  assert.match(assistant.formatError({ code: 'HTTPS_REQUIRED', message: 'HTTPS is required' }).title, /HTTPS/);
  assert.match(assistant.formatError({ name: 'SecurityError', message: 'A protected interface cannot be claimed' }).title, /interface USB/);
  const restriction = assistant.formatError({ code: 'INSTALL_FAILED', message: 'Failure [INSTALL_FAILED_USER_RESTRICTED: access denied by policy]' });
  assert.match(restriction.title, /política do aparelho bloqueou/);
  assert.doesNotMatch(restriction.title, /interface USB/);
  assert.match(assistant.formatError({ code: 'VERIFY_FAILED', message: 'access denied ao consultar a versão' }).title, /não pôde ser confirmada/);
});

test('APK rejeitado pelo Android e timeouts recebem diagnósticos específicos', () => {
  for (const message of ['Failure [INSTALL_FAILED_INVALID_APK]', 'Failure [INSTALL_PARSE_FAILED_BAD_MANIFEST]', 'PARSE_FAILED_NOT_APK']) assert.match(assistant.formatError({ code: 'INSTALL_FAILED', message }).title, /estrutura ou a leitura do APK/);
  assert.match(assistant.formatError({ code: 'INSTALL_FAILED', message: 'INSTALL_PARSE_FAILED_INCONSISTENT_CERTIFICATES' }).title, /assinatura/);
  assert.match(assistant.formatError({ code: 'AUTH_TIMEOUT', message: 'Aguardando dispositivo' }).title, /autorização/);
  assert.match(assistant.formatError({ code: 'CONNECT_TIMEOUT', message: 'Aguardando dispositivo' }).action, /RSA/);
  assert.match(assistant.formatError({ code: 'UPLOAD_TIMEOUT', message: 'Transferência parada' }).title, /transferência/);
  const sync = assistant.formatError({ code: 'SYNC_OPEN_TIMEOUT', message: 'O serviço sync não respondeu em 20 segundos.' });
  assert.match(sync.title, /abertura do canal/);
  assert.match(sync.action, /comando de instalação não foi enviado nesta tentativa/);
  assert.match(sync.action, /diagnóstico/);
});

// DOM mínimo: testa os handlers registrados por init, sem simular janelas nativas.
function domHarness(options) {
  options = options || {};
  const calls = { usb: 0, install: 0, load: 0, gesture: false, focused: null, scrolled: null };
  function element(id) {
    const listeners = new Map();
    return {
      id, dataset: {}, hidden: false, disabled: false, checked: false, open: false, textContent: '', value: '', files: [], children: [], attributes: {},
      addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(fn); },
      emit(type) { let result; for (const fn of listeners.get(type) || []) result = fn({ target: this }); return result; },
      focus() { calls.focused = id; }, scrollIntoView() { calls.scrolled = id; }, select() {},
      setAttribute(name, value) { this.attributes[name] = value; }, removeAttribute(name) { delete this.attributes[name]; },
      appendChild(child) { child.parent = this; this.children.push(child); return child; },
      remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); },
      get firstElementChild() { return this.children[0]; }
    };
  }
  const ids = ['apk', 'consent', 'cleanup-choice', 'connect', 'install', 'cancel', 'disconnect', 'reload', 'stage', 'next', 'file-status', 'device', 'device-text', 'progress-box', 'progress', 'progress-text', 'error', 'error-title', 'error-action', 'error-detail', 'log', 'finished', 'manual-check', 'connect-help', 'environment', 'console', 'copy-diagnostic', 'diagnostic-status', 'diagnostic-fallback'];
  const elements = Object.fromEntries(ids.map(id => ['aadx-usb-' + id, element('aadx-usb-' + id)]));
  const root = element('usb-instalacao');
  root.querySelector = selector => elements[selector.replace(/^#/, '')] || null;
  root.querySelectorAll = () => [];
  const document = { getElementById: id => id === 'usb-instalacao' ? root : elements[id] || null, querySelectorAll: () => [], createElement: tag => element(tag) };
  if (options.policy) document.permissionsPolicy = options.policy;
  const env = {
    isSecureContext: true, crypto: cryptoMock(), AbortController, location: { hash: options.hash || '' }, addEventListener() {},
    navigator: { usb: { requestDevice() { calls.usb += 1; assert.equal(calls.gesture, true, 'requestDevice deve acontecer durante o clique original'); if (options.usbError) return Promise.reject(options.usbError); return Promise.resolve({}); } } }
  };
  Object.assign(env, options.env || {});
  const client = { async model() { return { manufacturer: 'Teste', model: 'Modelo mock', apiLevel: 33, abis: ['arm64-v8a'], compatible: true }; }, async install() { calls.install += 1; return GOOD_RESULT; }, async close() { return { closed: true, forgotten: true, errors: [] }; } };
  const sdk = { createClient({ onStage }) { const promise = env.navigator.usb.requestDevice(); onStage({ stage: 'chooserRequested', message: 'Seleção USB solicitada ao navegador; se houver um seletor, escolha o Android.' }); return promise.then(() => client); } };
  const loadSdk = () => { calls.load += 1; return options.loadSdk ? options.loadSdk(sdk) : Promise.resolve(sdk); };
  const controller = assistant.init(document, env, { loadSdk });
  function click(id) { calls.gesture = true; try { return elements['aadx-usb-' + id].emit('click'); } finally { calls.gesture = false; } }
  return { calls, elements, root, document, env, sdk, controller, click };
}

test('gate síncrono exige ambiente, arquivo, consentimento e SDK sem supor política ausente', () => {
  const env = { isSecureContext: true, navigator: { usb: { requestDevice() {} } }, crypto: cryptoMock() };
  const state = { fileInfo: { valid: true }, sdkReady: true };
  assert.equal(assistant.getConnectionReadiness(env, state, true).code, 'READY');
  assert.equal(assistant.getConnectionReadiness(env, state, true, { permissionsPolicy: { allowsFeature() { throw new Error('Não disponível'); } } }).code, 'READY');
  assert.equal(assistant.getConnectionReadiness(env, state, true, { permissionsPolicy: { allowsFeature: feature => feature !== 'usb' } }).code, 'WEBUSB_POLICY');
  assert.equal(assistant.getConnectionReadiness({ ...env, navigator: {} }, state, true, { featurePolicy: { allowsFeature: () => false } }).code, 'WEBUSB_POLICY');
  assert.equal(assistant.getConnectionReadiness({ ...env, isSecureContext: false }, state, true).code, 'HTTPS_REQUIRED');
  assert.equal(assistant.getConnectionReadiness({ ...env, navigator: { usb: {} } }, state, true).code, 'WEBUSB_UNAVAILABLE');
  assert.equal(assistant.getConnectionReadiness({ ...env, crypto: {} }, state, true).code, 'CRYPTO_UNAVAILABLE');
  assert.equal(assistant.getConnectionReadiness(env, { ...state, busy: true }, true).code, 'BUSY');
  assert.equal(assistant.getConnectionReadiness(env, { ...state, validating: true }, true).code, 'APK_VALIDATING');
  assert.equal(assistant.getConnectionReadiness(env, { ...state, fileInfo: null }, true).code, 'NO_APK');
  assert.equal(assistant.getConnectionReadiness(env, state, false).code, 'CONSENT_REQUIRED');
  assert.equal(assistant.getConnectionReadiness(env, { ...state, sdkReady: false }, true).code, 'SDK_REQUIRED');
  assert.equal(assistant.getConnectionReadiness(env, { ...state, sdkReady: false, sdkLoading: true }, true).code, 'SDK_LOADING');
});

test('botão clicável explica arquivo e consentimento, foca o campo e registra bloqueio sem solicitar USB', async () => {
  const d = domHarness();
  assert.equal(d.elements['aadx-usb-connect'].disabled, false);
  d.click('connect');
  assert.equal(d.controller.getState().error.code, 'NO_APK');
  assert.equal(d.calls.focused, 'aadx-usb-apk'); assert.equal(d.calls.scrolled, 'aadx-usb-apk');
  assert.equal(d.calls.usb, 0); assert.equal(d.calls.load, 0);
  assert.match(assistant.buildDiagnostic(d.controller.getState()), /Pré-requisito \[NO_APK\]/);
  await d.controller.selectFile(fixture());
  d.click('connect');
  assert.equal(d.controller.getState().error.code, 'CONSENT_REQUIRED');
  assert.equal(d.calls.focused, 'aadx-usb-consent'); assert.equal(d.calls.scrolled, 'aadx-usb-consent');
  assert.equal(d.calls.usb, 0);
});

test('marcar consentimento resolve apenas seu pré-requisito sem alerta antigo contradizendo orientação', async () => {
  const d = domHarness(); await d.controller.prepare(); await d.controller.selectFile(fixture());
  d.click('connect'); assert.equal(d.controller.getState().error.code, 'CONSENT_REQUIRED');
  d.elements['aadx-usb-consent'].checked = true; d.elements['aadx-usb-consent'].emit('change');
  assert.equal(d.controller.getState().error, null);
  assert.equal(d.elements['aadx-usb-error'].hidden, true);
  assert.equal(d.elements['aadx-usb-connect-help'].dataset.ready, 'true');
  assert.match(d.elements['aadx-usb-stage'].textContent, /Tudo pronto/);
  assert.doesNotMatch(d.elements['aadx-usb-next'].textContent, /marque a confirmação/);
  assert.equal(d.calls.usb, 0);
});

test('preparação do SDK termina sem seletor e somente novo clique solicita USB ainda dentro do gesto', async () => {
  const loading = deferred(); const d = domHarness({ loadSdk: () => loading.promise });
  await d.controller.selectFile(fixture()); d.elements['aadx-usb-consent'].checked = true;
  d.click('connect');
  assert.equal(d.calls.load, 1); assert.equal(d.calls.usb, 0);
  assert.equal(d.controller.getState().sdkLoading, true);
  d.click('connect');
  assert.equal(d.calls.load, 1); assert.equal(d.calls.usb, 0);
  assert.match(d.elements['aadx-usb-connect-help'].textContent, /clique novamente/);
  loading.resolve(d.sdk); await turn();
  assert.equal(d.calls.usb, 0, 'resolver import não pode abrir seleção automaticamente');
  assert.equal(d.controller.getState().error, null);
  assert.equal(d.elements['aadx-usb-connect-help'].dataset.ready, 'true');
  d.click('connect');
  assert.equal(d.calls.usb, 1, 'solicitação precisa ocorrer antes do retorno do handler');
  assert.equal(d.elements['aadx-usb-connect'].disabled, true);
  assert.match(d.elements['aadx-usb-connect-help'].textContent, /solicitada ao navegador/);
  assert.doesNotMatch(d.elements['aadx-usb-connect-help'].textContent, /janela aberta|lista aberta/i);
  await turn();
  assert.equal(d.controller.getState().connected, true); assert.equal(d.calls.install, 0);
  assert.match(assistant.buildDiagnostic(d.controller.getState()), /Seleção USB solicitada ao navegador/);
  await d.controller.disconnect();
});

test('ambiente incompatível continua explicável por clique sem SDK ou solicitação nativa', () => {
  for (const option of [{ env: { isSecureContext: false }, code: 'HTTPS_REQUIRED' }, { env: { navigator: {} }, code: 'WEBUSB_UNAVAILABLE' }, { env: { crypto: {} }, code: 'CRYPTO_UNAVAILABLE' }, { policy: { allowsFeature: () => false }, code: 'WEBUSB_POLICY' }]) {
    const d = domHarness(option);
    assert.equal(d.elements['aadx-usb-connect'].disabled, false);
    d.click('connect');
    assert.equal(d.controller.getState().error.code, option.code);
    assert.equal(d.calls.usb, 0); assert.equal(d.calls.load, 0);
    assert.match(d.elements['aadx-usb-connect-help'].textContent, /HTTPS|Chrome|Edge/);
    assert.equal(d.elements['aadx-usb-connect-help'].dataset.ready, 'false');
    assert.ok(assistant.buildDiagnostic(d.controller.getState()).includes(option.code));
  }
});

test('rejeição real do seletor aparece no helper e checkbox não a apaga', async () => {
  const error = Object.assign(new Error('User activation is required'), { name: 'NotAllowedError' });
  const d = domHarness({ usbError: error }); await d.controller.prepare(); await d.controller.selectFile(fixture());
  d.elements['aadx-usb-consent'].checked = true; d.click('connect'); await turn();
  assert.equal(d.controller.getState().error.code, 'NotAllowedError');
  assert.match(d.elements['aadx-usb-connect-help'].textContent, /novo clique direto/);
  assert.equal(d.elements['aadx-usb-connect-help'].dataset.ready, 'false');
  assert.match(d.elements['aadx-usb-error-detail'].textContent, /User activation is required/);
  d.elements['aadx-usb-consent'].emit('change');
  assert.equal(d.controller.getState().error.code, 'NotAllowedError');
  assert.match(d.elements['aadx-usb-connect-help'].textContent, /novo clique direto/);
  assert.match(assistant.buildDiagnostic(d.controller.getState()), /NotAllowedError/);
});

test('falha real de SDK persiste após checkbox e não se converte em solicitação automática', async () => {
  const d = domHarness({ loadSdk: async () => { throw new Error('Componente indisponível para teste'); } });
  await d.controller.selectFile(fixture()); d.elements['aadx-usb-consent'].checked = true;
  d.click('connect'); await turn();
  assert.equal(d.controller.getState().error.code, 'SDK_LOAD');
  d.elements['aadx-usb-consent'].emit('change');
  assert.equal(d.controller.getState().error.code, 'SDK_LOAD'); assert.equal(d.calls.usb, 0);
  assert.match(d.elements['aadx-usb-error-detail'].textContent, /Componente indisponível/);
});

test('URL da seção abre preparação sem solicitar USB e init não duplica handler', async () => {
  const d = domHarness({ hash: '#usb-instalacao' });
  assert.equal(d.root.open, true); assert.equal(d.calls.load, 1); assert.equal(d.calls.usb, 0);
  assistant.init(d.document, d.env, { loadSdk: async () => { throw new Error('não executar'); } });
  await turn(); d.click('connect');
  assert.equal(d.controller.getState().logs.filter(entry => /Pré-requisito \[NO_APK\]/.test(entry.message)).length, 1);
});

test('falta de ativação por NotAllowedError e SecurityError recebe ação para novo clique', () => {
  for (const name of ['NotAllowedError', 'SecurityError']) {
    const formatted = assistant.formatError({ name, message: 'Must be handling a user gesture to show a permission request' });
    assert.match(formatted.title, /novo clique direto/);
    assert.match(formatted.action, /clique novamente em Conectar Android/);
    assert.doesNotMatch(formatted.title, /interface USB/);
  }
});
