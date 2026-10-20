'use strict';
// Behavioral checks with an in-memory DOM and USB responses, never physical USB.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const api = require('../assets/install-assistant.js');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');

test('brand and model searches select a guide without claiming an exact model', () => {
  for (const [query, expected] of [
    ['Samsung Galaxy A54', 'samsung'], ['SM-A546B/DS', 'samsung'],
    ['galaxya54', 'samsung'], ['SamsungA15', 'samsung'],
    ['Xiaomi Redmi Note 13', 'xiaomi'], ['POCOX6', 'xiaomi'],
    ['Mi 11 Lite', 'xiaomi'], ['Moto G54', 'motorola'],
    ['MOTOG54', 'motorola'], ['XT2343-2', 'motorola'],
    ['Pixel8a', 'pixel'], ['Google Pixel 9', 'pixel'],
    ['OPPO A54', 'generic'], ['ASUS Zenfone', 'generic'],
  ]) {
    const result = api.search(query);
    assert.equal(result.profileId, expected, query);
    assert.notEqual(result.confidence, 'exact', query);
  }
});

test('unknown, empty and conflicting searches do not invent a device', () => {
  assert.equal(api.search(null).confidence, 'none');
  assert.equal(api.search('A54').confidence, 'none');
  assert.equal(api.search('23122PCD1G').confidence, 'none');
  assert.equal(api.search('Samsung Redmi').confidence, 'ambiguous');
  assert.equal(api.search('Samsung Redmi').profileId, 'generic');
  assert.equal(api.normalize('  SAMSUNG / Galáxy-A54  '), 'samsung galaxy a54');
});

test('iPhone and iPad receive no Android installation guide', () => {
  for (const query of ['iPhone 14', 'Apple', 'iPadPro', 'iOS']) {
    assert.equal(api.search(query).confidence, 'unsupported');
    assert.equal(api.search(query).profileId, null);
  }
});

test('USB names and vendor identifiers remain uncertain suggestions', () => {
  assert.equal(api.USBtoGuide({vendorId: 0x04e8, productName: 'SM-A546B'}).profileId, 'samsung');
  assert.equal(api.USBtoGuide({vendorId: 0x2717, productName: 'MTP'}).evidence, 'vendor');
  assert.equal(api.USBtoGuide({vendorId: 0x18d1, manufacturerName: 'Google', productName: 'Android'}).profileId, 'generic');
  assert.equal(api.USBtoGuide({manufacturerName: 'Google', productName: 'Pixel 8'}).profileId, 'pixel');
  assert.equal(api.USBtoGuide({}).evidence, 'unknown');
  assert.equal(api.USBtoGuide({vendorId: -1, productId: '42'}).vendorId, null);
  assert.equal(api.USBtoGuide({manufacturerName: 'Apple', vendorId: 0x05ac}).evidence, 'unsupported');
});

test('conflicting USB descriptors do not silently select the wrong manufacturer', () => {
  for (const manufacturerName of ['Xiaomi', 'OPPO', 'Sony']) {
    const result = api.USBtoGuide({manufacturerName, productName: 'A54', vendorId: 0x04e8});
    assert.equal(result.conflict, true);
    assert.equal(result.profileId, 'generic');
  }
});

test('USB query is limited to the user chooser and four descriptors', async () => {
  const forbidden = new Set(['serialNumber', 'open', 'claimInterface', 'controlTransferOut', 'transferOut', 'transferIn']);
  const device = new Proxy({manufacturerName: 'Samsung', productName: 'SM-A546B', vendorId: 0x04e8, productId: 1}, {
    get(target, key) { assert.equal(forbidden.has(key), false, `Forbidden USB access: ${String(key)}`); return target[key]; },
  });
  let calls = 0;
  const result = await api.requestUsbIdentification({isSecureContext: true, navigator: {usb: {
    requestDevice(options) { calls++; assert.deepEqual(options, {filters: []}); return Promise.resolve(device); },
  }}});
  assert.equal(calls, 1);
  assert.equal(result.info.profileId, 'samsung');
});

test('unsupported or insecure contexts never request a device', async () => {
  let calls = 0;
  await assert.rejects(api.requestUsbIdentification({isSecureContext: false, navigator: {usb: {requestDevice() { calls++; }}}}), {name: 'SecurityError'});
  await assert.rejects(api.requestUsbIdentification({isSecureContext: true, navigator: {}}), {name: 'NotSupportedError'});
  assert.equal(calls, 0);
});

test('cancelled and denied USB selection keeps manual instructions available', async () => {
  for (const name of ['NotFoundError', 'NotAllowedError', 'SecurityError', 'NetworkError']) {
    const error = Object.assign(new Error('test'), {name});
    await assert.rejects(api.requestUsbIdentification({isSecureContext: true, navigator: {usb: {requestDevice() { return Promise.reject(error); }}}}), {name});
    assert.match(api.usbErrorMessage(error), /busca|buscar/i);
  }
});

test('steps apply to the APK source and distinguish Play Protect', () => {
  assert.match(api.makeSteps('samsung', 'chrome').join('\n'), /Chrome/);
  assert.match(api.makeSteps('xiaomi', 'files').join('\n'), /Arquivos/);
  assert.match(api.makeSteps('generic', 'unknown').join('\n'), /realmente abriu o APK/);
  assert.match(api.makeSteps('generic', 'chrome').join('\n'), /Play Protect.*não o remove/);
  assert.match(api.makeSteps('generic', 'chrome').at(-1), /desative a permissão/);
});

class Element {
  constructor(id = '') { this.id = id; this.dataset = {}; this.hidden = false; this.disabled = false; this.value = ''; this.textContent = ''; this.children = []; this.events = {}; }
  addEventListener(name, listener) { (this.events[name] ||= []).push(listener); }
  async emit(name, event = {}) { for (const listener of this.events[name] || []) await listener({preventDefault() {}, ...event}); }
  replaceChildren(...children) { this.children = children; }
  appendChild(child) { this.children.push(child); }
  focus() { this.focused = true; }
  select() { this.selected = true; }
}
function harness(options = {}) {
  const nodes = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element(match[1])]));
  const root = nodes['instalar-android'];
  const chips = ['samsung', 'xiaomi', 'motorola', 'pixel'].map(id => Object.assign(new Element(), {dataset: {aadxBrand: id}, textContent: id}));
  const progressive = [new Element()];
  root.querySelector = selector => nodes[selector.slice(1)] || null;
  root.querySelectorAll = selector => selector === '[data-aadx-js]' ? progressive : chips;
  nodes['aadx-source'].value = 'unknown';
  const document = {getElementById: id => nodes[id], createElement: () => new Element()};
  const usbEvents = {};
  let calls = 0;
  const usb = options.unsupported ? undefined : {
    requestDevice() { calls++; return options.requestDevice ? options.requestDevice() : Promise.resolve(options.device || {}); },
    addEventListener(name, fn) { usbEvents[name] = fn; },
  };
  const navigator = {usb, clipboard: options.clipboard};
  api.init(document, {isSecureContext: options.secure !== false, navigator});
  return {nodes, root, document, navigator, usbEvents, calls: () => calls};
}

test('search, source selection, clear and iOS recovery work through their UI handlers', async () => {
  const h = harness({unsupported: true});
  h.nodes['aadx-model'].value = 'Galaxy A54';
  await h.nodes['aadx-search-form'].emit('submit');
  assert.equal(h.nodes['aadx-brand'].value, 'samsung');
  h.nodes['aadx-source'].value = 'chrome';
  await h.nodes['aadx-source'].emit('change');
  assert.match(h.nodes['aadx-guide-steps'].children[2].textContent, /Chrome/);
  h.nodes['aadx-model'].value = 'iPhone';
  await h.nodes['aadx-search-form'].emit('submit');
  assert.equal(h.nodes['aadx-guide'].hidden, true);
  assert.equal(h.nodes['aadx-brand'].value, '');
  h.nodes['aadx-brand'].value = 'samsung';
  await h.nodes['aadx-brand'].emit('change');
  assert.equal(h.nodes['aadx-guide'].hidden, false);
  await h.nodes['aadx-clear'].emit('click');
  assert.equal(h.nodes['aadx-model'].value, '');
  assert.equal(h.nodes['aadx-brand'].value, 'generic');
});

test('USB unsupported or cancelled leaves the manual guide usable', async () => {
  const unavailable = harness({unsupported: true});
  assert.equal(unavailable.nodes['aadx-usb'].disabled, true);
  assert.equal(unavailable.nodes['aadx-guide'].hidden, false);
  const cancelled = harness({requestDevice: () => Promise.reject(Object.assign(new Error(), {name: 'NotFoundError'}))});
  await cancelled.nodes['aadx-usb'].emit('click');
  assert.equal(cancelled.nodes['aadx-usb'].disabled, false);
  assert.equal(cancelled.nodes['aadx-guide'].hidden, false);
  assert.match(cancelled.nodes['aadx-usb-status'].textContent, /Nenhum dispositivo/);
});

test('clipboard failure offers selectable text instead of claiming a successful copy', async () => {
  const h = harness({unsupported: true});
  await h.nodes['aadx-copy'].emit('click');
  assert.equal(h.nodes['aadx-copy-fallback'].hidden, false);
  assert.equal(h.nodes['aadx-copy-fallback'].selected, true);
  assert.match(h.nodes['aadx-copy-fallback'].value, /Permitir desta fonte/);
  assert.match(h.nodes['aadx-copy-status'].textContent, /não está disponível/);
});

test('USB descriptor text remains text and cable removal does not claim permission changes', async () => {
  const device = {manufacturerName: 'Samsung', productName: '<img src=x onerror=alert(1)>', vendorId: 0x04e8};
  const h = harness({device});
  await h.nodes['aadx-usb'].emit('click');
  assert.equal(h.nodes['aadx-usb-name'].textContent, device.productName);
  h.usbEvents.disconnect({device});
  assert.match(h.nodes['aadx-usb-status'].textContent, /desconectado/);
  assert.equal(h.nodes['aadx-guide'].hidden, false);
});

test('forgetting a device blocks a racing selection and preserves the next device', async () => {
  let finishForget;
  const pending = new Promise(resolve => { finishForget = resolve; });
  const a = {manufacturerName: 'Samsung', productName: 'A', vendorId: 0x04e8, forget: () => pending};
  const b = {manufacturerName: 'Xiaomi', productName: 'B', vendorId: 0x2717, forget: () => Promise.resolve()};
  let selected = a;
  const h = harness({requestDevice: () => Promise.resolve(selected)});
  await h.nodes['aadx-usb'].emit('click');
  const forgetting = h.nodes['aadx-usb-forget'].emit('click');
  assert.equal(h.nodes['aadx-usb'].disabled, true);
  selected = b;
  await h.nodes['aadx-usb'].emit('click');
  assert.equal(h.calls(), 1);
  finishForget();
  await forgetting;
  await h.nodes['aadx-usb'].emit('click');
  assert.equal(h.calls(), 2);
  assert.equal(h.nodes['aadx-usb-name'].textContent, 'B');
  assert.equal(h.nodes['aadx-usb-info'].hidden, false);
});
