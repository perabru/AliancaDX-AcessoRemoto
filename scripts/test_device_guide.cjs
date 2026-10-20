#!/usr/bin/env node
'use strict';
// Testes locais com descritores e DOM sintéticos. Não consultam celular real.
const test = require('node:test');
const assert = require('node:assert/strict');
const guide = require('../assets/device-guide.js');
const turn = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function environment(device) { return { isSecureContext: true, navigator: { usb: { requestDevice: async () => device || { manufacturerName: 'Samsung', productName: 'Galaxy A54', vendorId: 0x04e8, productId: 0x6860 } } } }; }

test('busca normaliza acentos e reconhece famílias sem afirmar modelo exato', () => {
  assert.equal(guide.normalize('  SÁMSUNG---Galaxy  '), 'samsung galaxy');
  for (const [query, expected] of [['SamsungA54', 'samsung'], ['Galaxy A54', 'samsung'], ['SM-A546B', 'samsung'], ['SM A546B', 'samsung'], ['REDMI Note13', 'xiaomi'], ['POCOX6', 'xiaomi'], ['Mi 11', 'xiaomi'], ['Moto G54', 'motorola'], ['XT2343', 'motorola'], ['Pixel8a', 'google']]) {
    const result = guide.search(query); assert.equal(result.profileId, expected, query); assert.equal(result.confidence, 'brand', query); assert.equal(result.model, undefined);
  }
  assert.equal(guide.search('A54').confidence, 'none');
  assert.equal(guide.search('OPPO A54').profileId, 'generic');
  assert.equal(guide.search('Samsung Pixel 8').confidence, 'ambiguous');
  assert.equal(guide.search('Código desconhecido 980').confidence, 'none');
  assert.equal(guide.search('').confidence, 'none');
  for (const value of ['iPhone15', 'iPad', 'iOS', 'Apple']) assert.equal(guide.search(value).confidence, 'unsupported');
});

test('descritores têm VID:PID não exclusivo e sugestão de marca sem catálogo de modelo', () => {
  const samsung = guide.USBtoGuide({ manufacturerName: '', productName: '', vendorId: 0x04e8, productId: 0x6860, serialNumber: 'NAO_LER' });
  assert.equal(samsung.profileId, 'samsung'); assert.equal(samsung.evidence, 'vendor'); assert.equal(samsung.identifier, '04E8:6860'); assert.equal(samsung.serialNumber, undefined);
  assert.equal(guide.USBtoGuide({ vendorId: 0x18d1, productId: 4 }).profileId, 'generic');
  assert.equal(guide.USBtoGuide({ manufacturerName: 'Google', productName: 'Android MTP', vendorId: 0x18d1 }).profileId, 'generic');
  assert.equal(guide.USBtoGuide({ manufacturerName: 'Google', productName: 'Pixel 8a', vendorId: 0x18d1 }).profileId, 'google');
  assert.equal(guide.USBtoGuide({ manufacturerName: 'Motorola', vendorId: 0x04e8 }).evidence, 'conflict');
  assert.equal(guide.USBtoGuide({ manufacturerName: 'Google', productName: 'Android', vendorId: 0x04e8 }).evidence, 'conflict');
  assert.equal(guide.USBtoGuide({ manufacturerName: 'OPPO', vendorId: 0x04e8 }).profileId, 'generic');
  assert.equal(guide.USBtoGuide({ manufacturerName: 'Apple', vendorId: 0x05ac }).evidence, 'unsupported');
  assert.equal(guide.USBtoGuide({ vendorId: -1, productId: 70000 }).identifier, 'não informado');
  assert.equal(guide.USBtoGuide({ vendorId: 0, productId: 0 }).identifier, '0000:0000');
  assert.equal(guide.USBtoGuide({ manufacturerName: '\0 Samsung \n', productName: 'x'.repeat(300) }).productName.length, 120);
});

test('identificação lê somente quatro descritores e não abre interfaces ou lê número de série', async () => {
  const reads = [], values = { manufacturerName: 'Motorola', productName: 'Moto G54', vendorId: 0x22b8, productId: 0x2e82 };
  const device = new Proxy({}, { get(_target, key) { if (key === 'then') return undefined; reads.push(key); if (!(key in values)) throw new Error('Acesso USB proibido: ' + String(key)); return values[key]; } });
  let options;
  const env = environment(); env.navigator.usb.requestDevice = async value => { options = value; return device; };
  const result = await guide.requestUsbIdentification(env);
  assert.deepEqual(options, { filters: [] });
  assert.deepEqual(reads, ['manufacturerName', 'productName', 'vendorId', 'productId']);
  assert.equal(result.info.profileId, 'motorola'); assert.equal(result.info.identifier, '22B8:2E82');
});

test('HTTPS e política USB são verificados antes de solicitar e API de política ausente não bloqueia', async () => {
  let requested = 0; const env = environment(); env.navigator.usb.requestDevice = () => { requested += 1; return Promise.resolve({}); };
  const policy = { permissionsPolicy: { allowsFeature: feature => feature !== 'usb' } };
  assert.equal(guide.getUsbReadiness({ ...env, navigator: {} }, policy).code, 'USB_POLICY');
  await assert.rejects(guide.requestUsbIdentification(env, policy), { code: 'USB_POLICY' });
  await assert.rejects(guide.requestUsbIdentification({ ...env, isSecureContext: false }), { code: 'HTTPS_REQUIRED' });
  await assert.rejects(guide.requestUsbIdentification({ ...env, navigator: {} }), { code: 'USB_UNAVAILABLE' });
  assert.equal(requested, 0);
  assert.equal(guide.getUsbReadiness(env).ready, true);
  assert.equal(guide.getUsbReadiness(env, { featurePolicy: { allowsFeature() { throw new Error('API inconclusiva'); } } }).ready, true);
});

test('cancelamento, recusa, desconexão e falta de gesto mantêm opção manual', () => {
  for (const name of ['NotFoundError', 'SecurityError', 'NotAllowedError', 'NetworkError', 'Error']) assert.match(guide.formatUsbError({ name }).message, /manual|busca/);
  for (const name of ['SecurityError', 'NotAllowedError']) assert.match(guide.formatUsbError({ name, message: 'Must be handling a user gesture' }).message, /clique direto/);
});

test('quatro passos distinguem descritores, desenvolvimento, RSA opcional e desligamento manual', () => {
  for (const profile of guide.profiles) {
    const steps = guide.buildSteps(profile.id); assert.equal(steps.length, 4);
    assert.match(steps[0].note, /sem.*depuração USB/);
    assert.match(steps[1].steps.join(' '), /sete vezes/);
    assert.match(steps[1].steps.join(' '), /Nunca informe esses dados neste site/);
    assert.match(steps[2].steps.join(' '), /não usa ADB e não solicita autorização RSA/);
    assert.match(steps[3].note, /não detecta nem confirma/);
    assert.doesNotMatch(JSON.stringify(steps), /desative o Play Protect|Instalar via USB|OEM unlocking|desbloqueio OEM/i);
  }
  assert.match(guide.buildSteps('xiaomi')[1].note, /HyperOS.*MIUI/);
  assert.match(guide.buildSteps('xiaomi')[1].steps[1], /Versão MIUI/);
  assert.doesNotMatch(guide.buildSteps('samsung')[1].steps.join(' '), /Xiaomi|MIUI|HyperOS/);
  assert.match(guide.buildSteps('generic')[1].note, /guia geral/);
});

function domHarness(options) {
  options = options || {}; const ids = new Map(), calls = { usb: 0, gesture: false, copied: [], forgotten: 0, focused: null, showModal: 0, close: 0 };
  const document = { activeElement: null, querySelectorAll: selector => selector === '[data-adx-js]' ? ['adx-device-actions', 'adx-device-form', 'adx-device-manual'].map(id => ids.get(id)) : [], getElementById: id => ids.get(id) || null };
  function element(tag, id) {
    const listeners = new Map(); let currentId = '';
    const node = { tagName: tag.toUpperCase(), dataset: {}, hidden: false, disabled: false, checked: false, value: '', textContent: '', children: [], attributes: {}, open: false,
      get id() { return currentId; }, set id(value) { currentId = value; ids.set(value, this); },
      addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(fn); },
      emit(type, args) { if (type === 'click' && this.disabled) return; let result; for (const handler of listeners.get(type) || []) result = handler(Object.assign({ target: this, preventDefault() {} }, args)); return result; },
      appendChild(child) { this.children.push(child); child.parent = this; return child; },
      removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parent = null; return child; },
      get firstChild() { return this.children[0] || null; },
      setAttribute(name, value) { this.attributes[name] = value; if (name === 'open') this.open = true; },
      removeAttribute(name) { delete this.attributes[name]; if (name === 'open') this.open = false; },
      focus() { calls.focused = this.id; document.activeElement = this; }, select() { this.selected = true; },
      getBoundingClientRect() { return { left: 10, right: 100, top: 10, bottom: 100 }; },
      showModal() { calls.showModal += 1; this.open = true; }, close() { calls.close += 1; this.open = false; this.emit('close'); }
    };
    if (id) node.id = id; return node;
  }
  document.createElement = tag => element(tag);
  const names = ['device-form', 'device-search', 'device-brand', 'device-model', 'device-usb', 'device-status', 'device-manual', 'device-forget', 'device-dialog', 'guide-body', 'guide-title', 'guide-note', 'guide-source', 'guide-step-label', 'guide-back', 'guide-next', 'guide-copy', 'guide-close', 'guide-copy-status', 'guide-fallback'];
  names.forEach(name => element(name.includes('dialog') ? 'dialog' : 'div', 'adx-' + name));
  element('div', 'adx-device-actions');
  ['adx-device-actions', 'adx-device-form', 'adx-device-manual', 'adx-device-forget'].forEach(id => { ids.get(id).hidden = true; });
  if (options.noDialog) delete ids.get('adx-device-dialog').showModal;
  if (options.dialogError) ids.get('adx-device-dialog').showModal = () => { throw new Error('Dialog indisponível'); };
  ids.get('adx-device-brand').value = 'auto';
  const device = options.device || { manufacturerName: 'Samsung', productName: 'Galaxy A54', vendorId: 0x04e8, productId: 0x6860, async forget() { calls.forgotten += 1; } };
  const env = environment(device);
  env.navigator.usb.requestDevice = function (filters) { calls.usb += 1; assert.equal(calls.gesture, true, 'requestDevice preserva gesto síncrono'); assert.deepEqual(filters, { filters: [] }); if (options.usbError) return Promise.reject(options.usbError); return options.pending || Promise.resolve(device); };
  if (!options.noClipboard) env.navigator.clipboard = { async writeText(value) { calls.copied.push(value); } };
  if (options.env) Object.assign(env, options.env);
  const controller = guide.init(document, env);
  function click(id, args) { calls.gesture = true; try { return ids.get('adx-' + id).emit('click', args); } finally { calls.gesture = false; } }
  const content = node => node.textContent + '\n' + node.children.map(content).join('\n');
  return { calls, document, env, ids, controller, click, content, device };
}

test('USB pede seleção no clique original e abre diálogo com VID:PID sem afirmar modelo verificado', async () => {
  const d = domHarness(); const pending = d.click('device-usb');
  assert.equal(d.calls.usb, 1); assert.equal(d.ids.get('adx-device-usb').disabled, true);
  assert.match(d.ids.get('adx-device-status').textContent, /Se ele mostrar um seletor/);
  await pending;
  assert.equal(d.ids.get('adx-device-dialog').open, true); assert.equal(d.calls.showModal, 1);
  const body = d.content(d.ids.get('adx-guide-body'));
  assert.match(body, /Galaxy A54/); assert.match(body, /04E8:6860/); assert.match(body, /não é número de série/);
  assert.equal(d.controller.getState().confirmedModel, '');
  assert.equal(d.ids.get('adx-guide-brand').value, 'samsung');
  assert.match(d.ids.get('adx-device-status').textContent, /modelo exato.*não foram verificados/);
  assert.equal(d.ids.get('adx-device-forget').hidden, false);
  assert.equal(d.ids.get('adx-device-usb').disabled, false);
});

test('descritores genéricos exigem confirmação da marca no diálogo antes de prosseguir', async () => {
  const d = domHarness({ device: { manufacturerName: '', productName: 'MTP', vendorId: 0x18d1, productId: 4 } });
  await d.click('device-usb');
  assert.equal(d.ids.get('adx-guide-brand').value, ''); assert.equal(d.ids.get('adx-guide-next').disabled, true);
  const brand = d.ids.get('adx-guide-brand'); brand.value = 'generic'; brand.emit('change');
  assert.equal(d.ids.get('adx-guide-next').disabled, false);
  d.click('guide-next'); assert.equal(d.controller.getState().step, 1);
  assert.match(d.ids.get('adx-guide-note').textContent, /guia geral/);
});

test('guia manual confirma marca e modelo sem USB; navegação possui quatro etapas e restaura foco', () => {
  const d = domHarness(); d.ids.get('adx-device-search').value = 'Moto G54'; d.ids.get('adx-device-model').value = 'Moto G54 informado';
  d.click('device-manual');
  assert.equal(d.calls.usb, 0); assert.equal(d.ids.get('adx-guide-brand').value, 'motorola');
  const model = d.ids.get('adx-guide-model'); model.value = 'Meu Moto G54'; model.emit('input');
  d.click('guide-next'); assert.equal(d.ids.get('adx-device-dialog').dataset.step, '2');
  assert.match(d.content(d.ids.get('adx-guide-body')), /Meu Moto G54/);
  assert.match(d.content(d.ids.get('adx-guide-body')), /sete vezes/);
  d.click('guide-next'); assert.equal(d.ids.get('adx-device-dialog').dataset.step, '3');
  assert.match(d.content(d.ids.get('adx-guide-body')), /não solicita autorização RSA/);
  d.click('guide-back'); assert.equal(d.controller.getState().step, 1);
  d.click('guide-next'); d.click('guide-next'); assert.equal(d.ids.get('adx-guide-next').textContent, 'Concluir');
  assert.match(d.ids.get('adx-guide-note').textContent, /conferência é manual/);
  d.click('guide-next'); assert.equal(d.ids.get('adx-device-dialog').open, false); assert.equal(d.calls.focused, 'adx-device-manual');
});

test('cancelar USB ou ambiente sem WebUSB preserva botão manual e não simula identificação', async () => {
  for (const options of [{ usbError: Object.assign(new Error('Cancelado'), { name: 'NotFoundError' }) }, { env: { navigator: {} } }, { env: { isSecureContext: false } }]) {
    const d = domHarness(options); await d.click('device-usb');
    assert.equal(d.ids.get('adx-device-dialog').open, false); assert.equal(d.controller.getState().usbInfo, null);
    assert.equal(d.ids.get('adx-device-manual').hidden, false); assert.equal(d.ids.get('adx-device-usb').disabled, false);
    d.ids.get('adx-device-brand').value = 'generic'; d.click('device-manual');
    assert.equal(d.ids.get('adx-device-dialog').open, true);
  }
});

test('busca ou descritor Apple não abre automaticamente guia Android', async () => {
  const d = domHarness(); d.ids.get('adx-device-search').value = 'iPhone15'; d.click('device-manual');
  assert.equal(d.ids.get('adx-device-dialog').open, false); assert.match(d.ids.get('adx-device-status').textContent, /iOS/);
  d.ids.get('adx-device-brand').value = 'samsung'; d.click('device-manual'); assert.equal(d.ids.get('adx-device-dialog').open, true);
  const apple = domHarness({ device: { manufacturerName: 'Apple', productName: 'iPhone', vendorId: 0x05ac, productId: 0x12a8 } });
  await apple.click('device-usb'); assert.equal(apple.ids.get('adx-device-dialog').open, false); assert.match(apple.ids.get('adx-device-status').textContent, /Apple/);
});

test('resultado USB tardio não sobrescreve guia escolhido manualmente durante o seletor', async () => {
  const pending = deferred(), d = domHarness({ pending: pending.promise });
  const operation = d.click('device-usb');
  d.ids.get('adx-device-brand').value = 'motorola'; d.click('device-manual');
  pending.resolve(d.device); await operation;
  assert.equal(d.controller.getState().profileId, 'motorola'); assert.equal(d.controller.getState().usbInfo, null);
  assert.equal(d.calls.showModal, 1); assert.equal(d.ids.get('adx-device-usb').disabled, false);
});

test('Escape, fechar e clique de fundo restauram foco sem confundir conteúdo com backdrop', () => {
  const d = domHarness(); d.ids.get('adx-device-brand').value = 'generic'; d.click('device-manual');
  d.ids.get('adx-device-dialog').emit('click', { target: d.ids.get('adx-device-dialog'), clientX: 50, clientY: 50 });
  assert.equal(d.ids.get('adx-device-dialog').open, true);
  d.ids.get('adx-device-dialog').emit('cancel'); assert.equal(d.ids.get('adx-device-dialog').open, false); assert.equal(d.calls.focused, 'adx-device-manual');
  d.click('device-manual'); d.ids.get('adx-device-dialog').emit('click', { target: d.ids.get('adx-device-dialog'), clientX: 0, clientY: 0 }); assert.equal(d.ids.get('adx-device-dialog').open, false);
  d.click('device-manual'); d.click('guide-close'); assert.equal(d.ids.get('adx-device-dialog').open, false);
});

test('copiar guia usa clipboard e fallback selecionável sem alegar cópia concluída', async () => {
  const d = domHarness(); d.ids.get('adx-device-brand').value = 'samsung'; d.click('device-manual'); await d.click('guide-copy');
  assert.equal(d.calls.copied.length, 1); assert.match(d.calls.copied[0], /Número de compilação/); assert.match(d.calls.copied[0], /Fonte: https/);
  const fallback = domHarness({ noClipboard: true }); fallback.ids.get('adx-device-brand').value = 'generic'; fallback.click('device-manual'); await fallback.click('guide-copy');
  assert.match(fallback.ids.get('adx-guide-copy-status').textContent, /cópia automática não está disponível/);
  const textarea = fallback.ids.get('adx-device-dialog').children.find(child => child.tagName === 'TEXTAREA');
  assert.ok(textarea); assert.equal(textarea.readOnly, true); assert.equal(textarea.selected, true); assert.match(textarea.value, /guia manual Android/);
  fallback.click('guide-next'); assert.equal(textarea.hidden, true);
});

test('esquecer USB é ação explícita do navegador, sem alegar desligamento no Android', async () => {
  const d = domHarness(); await d.click('device-usb'); assert.equal(d.calls.forgotten, 0);
  d.click('guide-close'); await d.click('device-forget');
  assert.equal(d.calls.forgotten, 1); assert.equal(d.ids.get('adx-device-forget').hidden, true);
  assert.match(d.ids.get('adx-device-status').textContent, /Nenhuma configuração do Android foi alterada/);
});

test('esquecer dispositivo anterior não apaga autorização de nova identificação concorrente', async () => {
  const forgetting = deferred(); let secondForgotten = 0;
  const first = { manufacturerName: 'Samsung', productName: 'Aparelho A', vendorId: 0x04e8, productId: 1, forget: () => forgetting.promise };
  const second = { manufacturerName: 'Motorola', productName: 'Aparelho B', vendorId: 0x22b8, productId: 2, async forget() { secondForgotten += 1; } };
  const d = domHarness({ device: first }); await d.click('device-usb'); d.click('guide-close');
  const pendingForget = d.click('device-forget');
  d.env.navigator.usb.requestDevice = () => Promise.resolve(second);
  await d.click('device-usb'); const latestStatus = d.ids.get('adx-device-status').textContent; forgetting.resolve(); await pendingForget;
  assert.equal(d.controller.getState().usbInfo.productName, 'Aparelho B');
  assert.equal(d.ids.get('adx-device-forget').hidden, false);
  assert.equal(d.ids.get('adx-device-status').textContent, latestStatus);
  d.click('guide-close'); await d.click('device-forget'); assert.equal(secondForgotten, 1);
});

test('esquecer seleção anterior não oculta nova seleção do mesmo objeto USBDevice', async () => {
  const forgetting = deferred(); let forgot = 0;
  const device = { manufacturerName: 'Samsung', productName: 'Galaxy A', vendorId: 0x04e8, productId: 1, forget() { forgot += 1; return forgot === 1 ? forgetting.promise : Promise.resolve(); } };
  const d = domHarness({ device }); await d.click('device-usb'); d.click('guide-close');
  const pendingForget = d.click('device-forget');
  await d.click('device-usb');
  const latestStatus = d.ids.get('adx-device-status').textContent;
  forgetting.resolve(); await pendingForget;
  assert.equal(d.controller.getState().usbInfo.productName, 'Galaxy A');
  assert.equal(d.ids.get('adx-device-forget').hidden, false);
  assert.equal(d.ids.get('adx-device-status').textContent, latestStatus);
  d.click('guide-close'); await d.click('device-forget'); assert.equal(forgot, 2);
});

test('sem showModal nativo abre guia de leitura e não simula modal sem foco protegido', () => {
  for (const options of [{ noDialog: true }, { dialogError: true }]) {
    const d = domHarness(options); d.ids.get('adx-device-brand').value = 'generic';
    d.click('device-manual');
    assert.equal(d.ids.get('adx-device-dialog').open, false);
    assert.equal(d.ids.get('adx-device-dialog').attributes['aria-modal'], undefined);
    assert.equal(d.ids.get('adx-device-dialog').attributes.role, undefined);
    assert.equal(d.ids.get('adx-guide-fallback').open, true);
    assert.equal(d.calls.focused, 'adx-guide-fallback');
    assert.match(d.ids.get('adx-device-status').textContent, /guia de leitura/);
    assert.equal(d.calls.usb, 0);
  }
});

test('hash copiado mantém 64hex completos e falha abre detalhe, sem comando legado', async () => {
  const received = [], controls = { button: { disabled: false }, full: { open: false }, status: { textContent: '' } };
  assert.equal(await guide.copyHash(controls, { navigator: { clipboard: { async writeText(value) { received.push(value); } } } }), true);
  assert.deepEqual(received, [guide.APK_SHA256]); assert.match(received[0], /^[a-f0-9]{64}$/);
  assert.equal(await guide.copyHash(controls, {}), false); assert.equal(controls.full.open, true); assert.match(controls.status.textContent, /Não foi possível/);
});

test('init é idempotente e texto de usuário é exibido por textContent', () => {
  const d = domHarness(); guide.init(d.document, d.env);
  d.ids.get('adx-device-model').value = '<img src=x onerror=alert(1)>'; d.ids.get('adx-device-brand').value = 'generic'; d.click('device-manual');
  assert.equal(d.calls.showModal, 1); d.click('guide-next');
  assert.match(d.content(d.ids.get('adx-guide-body')), /<img src=x onerror=alert\(1\)>/);
});

test('init revela ações marcadas para JavaScript sem expor esquecer USB antes da autorização', () => {
  const d = domHarness();
  for (const id of ['adx-device-actions', 'adx-device-form', 'adx-device-manual']) assert.equal(d.ids.get(id).hidden, false, id);
  assert.equal(d.ids.get('adx-device-forget').hidden, true);
  assert.equal(d.calls.usb, 0);
});

test('hash do cartão funciona sem formulário de guia e não recebe handlers duplicados', async () => {
  const copied = [], handlers = [], button = { hidden: true, addEventListener(type, handler) { assert.equal(type, 'click'); handlers.push(handler); } };
  const full = { open: false }, status = { textContent: '' };
  const group = { dataset: {}, querySelector(selector) { return { '[data-aadx-hash-copy]': button, '[data-aadx-hash-full]': full, '[data-aadx-hash-status]': status }[selector] || null; } };
  const document = { querySelectorAll: () => [group], getElementById: () => null };
  const env = { navigator: { clipboard: { async writeText(value) { copied.push(value); } } } };
  guide.init(document, env); guide.init(document, env);
  assert.equal(button.hidden, false); assert.equal(handlers.length, 1);
  await handlers[0](); assert.deepEqual(copied, [guide.APK_SHA256]);
});
