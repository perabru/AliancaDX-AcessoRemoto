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
  h.timers.fire(30000); assert.match(history(h), /Ainda aguardando/);
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
