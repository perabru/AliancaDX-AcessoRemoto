/* AliançaDX — guia Android local. USB somente para descritores, sem ADB. */
(function (global, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (global && global.document) {
    global.AliancaDXDeviceGuide = api;
    const start = () => api.init(global.document, global);
    if (global.document.readyState === 'loading') global.document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const APK_SHA256 = '3ff081e7c6244be6c07d5a45d658f05c845d052bbc7064deec8b736e89379f3c';
  const ANDROID_SOURCE = 'https://developer.android.com/studio/debug/dev-options';
  const profiles = Object.freeze([
    { id: 'samsung', label: 'Samsung · Galaxy', aliases: ['samsung', 'galaxy'], pattern: /\bsm\s?[a-z]\d{3,}[a-z0-9]*\b/,
      developerPath: 'Configurações → Sobre o telefone → Informações do software → Número de compilação',
      debugPath: 'Configurações → Opções do desenvolvedor → Depuração USB',
      variant: 'Os nomes e a posição dos menus podem variar conforme a versão da One UI.',
      source: 'https://www.samsung.com/uk/support/mobile-devices/how-do-i-turn-on-the-developer-options-menu-on-my-samsung-galaxy-device/', sourceLabel: 'Guia oficial Samsung' },
    { id: 'xiaomi', label: 'Xiaomi · Redmi · POCO', aliases: ['xiaomi', 'redmi', 'poco', 'miui', 'hyperos'], pattern: /\bmi\s?\d{1,2}[a-z0-9]*\b/,
      developerPath: 'Configurações → Sobre o telefone → Versão do SO (HyperOS)',
      debugPath: 'Configurações → Configurações adicionais → Opções do desenvolvedor → Depuração USB',
      variant: 'No HyperOS, a versão do SO pode estar em “Informações detalhadas e especificações”. Na MIUI, procure “Meu dispositivo / Sobre o telefone → Todas as especificações → Versão MIUI”.',
      source: 'https://www.mi.com/global/support/faq/details/KA-07878/', sourceLabel: 'Guia oficial Xiaomi',
      additionalSources: [{ url: 'https://www.mi.com/global/support/article/KA-06515/', label: 'Xiaomi: menus MIUI e depuração USB' }, { url: 'https://www.mi.com/global/support/faq/details/KA-168649/', label: 'Xiaomi: opções do desenvolvedor' }] },
    { id: 'motorola', label: 'Motorola · Moto', aliases: ['motorola', 'moto'], pattern: /\bxt\s?\d{4}[a-z0-9]*\b/,
      developerPath: 'Configurar → Sobre o telefone → Número da versão / Número de compilação',
      debugPath: 'Configurar → Sistema → Opções do desenvolvedor → Depuração USB',
      variant: 'Algumas versões colocam “Sobre o telefone” em “Sistema” e “Opções do desenvolvedor” em “Sistema → Avançado”.',
      source: 'https://help.motorola.com/hc/apps/service/rsa/58/en-us/CG1590266220.html', sourceLabel: 'Guia oficial Motorola' },
    { id: 'google', label: 'Google · Pixel', aliases: ['google', 'pixel'],
      developerPath: 'Configurações → Sobre o telefone → Número de compilação',
      debugPath: 'Configurações → Sistema → Opções do desenvolvedor → Depuração USB',
      variant: 'Em algumas versões do Android, abra “Sistema → Avançado” antes de “Opções do desenvolvedor”.',
      source: ANDROID_SOURCE, sourceLabel: 'Guia oficial Android / Google' },
    { id: 'generic', label: 'Outra marca · Guia geral Android', aliases: ['android', 'oppo', 'realme', 'vivo', 'asus', 'zenfone', 'rog phone', 'sony', 'xperia', 'huawei', 'honor', 'nokia', 'nothing', 'infinix', 'tecno'],
      developerPath: 'Configurações → Sobre o telefone → Número da versão / Número de compilação',
      debugPath: 'Configurações → procure “Opções do desenvolvedor” → Depuração USB',
      variant: 'Este é um guia geral, não um caminho confirmado para esse modelo. Use a busca das Configurações ou o suporte oficial da sua marca se os nomes forem diferentes.',
      source: ANDROID_SOURCE, sourceLabel: 'Referência oficial do Android' }
  ]);
  // O VID Google 18D1 também é usado por outros fabricantes: não comprova Pixel.
  const vendorProfiles = Object.freeze({ 0x04e8: 'samsung', 0x2717: 'xiaomi', 0x22b8: 'motorola' });
  function text(value, limit) { return String(value == null ? '' : value).replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, limit || 140); }
  function normalize(value) { return String(value == null ? '' : value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().replace(/\s+/g, ' '); }
  function profileById(id) { return profiles.find(profile => profile.id === id) || profiles[profiles.length - 1]; }
  function search(value) {
    const query = normalize(value);
    if (!query) return { query, profileId: 'generic', confidence: 'none', matches: [] };
    if (/\b(?:i\s?phone[a-z0-9]*|ipad[a-z0-9]*|ios|apple)\b/.test(query)) return { query, profileId: null, confidence: 'unsupported', platform: 'ios', matches: [] };
    const matches = profiles.filter(profile => profile.aliases.some(alias => {
      const a = normalize(alias);
      return (' ' + query + ' ').includes(' ' + a + ' ') || (a.length >= 4 && query.replace(/\s/g, '').startsWith(a.replace(/\s/g, '')));
    }) || profile.pattern && profile.pattern.test(query));
    const specific = matches.filter(profile => profile.id !== 'generic');
    const selected = specific.length === 1 ? specific[0] : specific.length === 0 && matches.length ? profileById('generic') : null;
    return { query, profileId: selected ? selected.id : 'generic', confidence: selected ? selected.id === 'generic' ? 'generic' : 'brand' : specific.length > 1 ? 'ambiguous' : 'none', matches: (specific.length ? specific : matches).map(profile => ({ id: profile.id, label: profile.label })) };
  }
  function hex(value) { return Number.isInteger(value) && value >= 0 && value <= 65535 ? value.toString(16).padStart(4, '0').toUpperCase() : null; }
  function USBtoGuide(info) {
    const manufacturerName = text(info && info.manufacturerName, 120), productName = text(info && info.productName, 120);
    const vendorId = hex(info && info.vendorId) ? info.vendorId : null, productId = hex(info && info.productId) ? info.productId : null;
    const byName = search(manufacturerName + ' ' + productName), byVendor = vendorProfiles[vendorId];
    const base = { manufacturerName, productName, vendorId, productId, identifier: vendorId !== null && productId !== null ? hex(vendorId) + ':' + hex(productId) : 'não informado' };
    if (byName.confidence === 'unsupported' || vendorId === 0x05ac) return { ...base, profileId: null, evidence: 'unsupported', conflict: false };
    let named = byName.confidence === 'brand' ? byName.profileId : null;
    if (named === 'google' && !/\bpixel[a-z0-9]*\b/.test(normalize(productName))) named = null;
    const names = ' ' + normalize(manufacturerName + ' ' + productName) + ' ';
    const otherMaker = profileById('generic').aliases.some(alias => alias !== 'android' && names.includes(' ' + normalize(alias) + ' '));
    const makerHint = search(manufacturerName);
    const conflict = Boolean(named && byVendor && named !== byVendor) || Boolean(makerHint.confidence === 'brand' && byVendor && makerHint.profileId !== byVendor) || Boolean(otherMaker && byVendor) || byName.confidence === 'ambiguous';
    return { ...base, profileId: conflict ? 'generic' : named || byVendor || 'generic', evidence: conflict ? 'conflict' : named ? 'descriptor' : byVendor ? 'vendor' : 'unknown', conflict };
  }
  function getUsbReadiness(env, document) {
    env = env || {};
    if (!env.isSecureContext) return { ready: false, code: 'HTTPS_REQUIRED', message: 'A identificação USB exige HTTPS. Abra o site diretamente em uma aba completa do Chrome ou Edge no computador. O guia manual continua disponível.' };
    try {
      const doc = document || env.document, policy = doc && (doc.permissionsPolicy || doc.featurePolicy);
      if (policy && typeof policy.allowsFeature === 'function' && policy.allowsFeature('usb') === false) return { ready: false, code: 'USB_POLICY', message: 'O navegador bloqueou USB nesta página. Abra o site diretamente em uma aba completa do Chrome ou Edge no computador. Você pode abrir o guia manual agora.' };
    } catch (_) { /* Uma consulta ausente ou inconclusiva não prova bloqueio. */ }
    if (!env.navigator || !env.navigator.usb || typeof env.navigator.usb.requestDevice !== 'function') return { ready: false, code: 'USB_UNAVAILABLE', message: 'Este navegador não oferece identificação WebUSB. No computador, abra o site no Chrome ou Edge; a busca e o guia manual funcionam sem USB.' };
    return { ready: true, code: 'READY', message: 'O USB consulta somente os nomes e os códigos informados pelo dispositivo. Não exige opções do desenvolvedor, não instala aplicativos e não altera o celular.' };
  }
  function formatUsbError(error) {
    const code = String(error && (typeof error.code === 'string' && error.code || error.name) || 'USB_ERROR');
    if (code === 'NotFoundError') return { code, message: 'Nenhum dispositivo foi selecionado. Tente novamente ou abra o guia manual. Nenhuma configuração do celular foi alterada.' };
    if (/user (?:gesture|activation)|transient activation|handling a user gesture/i.test(String(error && error.message || ''))) return { code, message: 'O navegador exige um clique direto. Clique novamente em Identificar por USB nesta aba ou abra o guia manual.' };
    if (/SecurityError|NotAllowedError|USB_POLICY/.test(code)) return { code, message: 'O navegador não autorizou a consulta USB. Confira as permissões USB do site e abra a página diretamente em Chrome ou Edge no computador. O guia manual continua disponível.' };
    if (/NetworkError|DISCONNECTED/.test(code)) return { code, message: 'Não foi possível consultar o dispositivo. Confira o cabo de dados e o celular selecionado ou use o guia manual. Nenhuma configuração foi alterada.' };
    if (/USB_UNAVAILABLE|NotSupportedError/.test(code)) return { code, message: 'A consulta USB não está disponível neste navegador. Use Chrome ou Edge no computador ou abra o guia manual.' };
    return { code, message: 'Não foi possível obter os descritores USB. Use a busca ou o guia manual. A página não alterou configurações do aparelho.' };
  }
  async function requestUsbIdentification(env, document) {
    const readiness = getUsbReadiness(env, document);
    if (!readiness.ready) { const error = new Error(readiness.message); error.code = readiness.code; throw error; }
    // O clique chega a requestDevice antes do primeiro await. Não abrimos o dispositivo.
    const device = await env.navigator.usb.requestDevice({ filters: [] });
    const info = USBtoGuide({ manufacturerName: device.manufacturerName, productName: device.productName, vendorId: device.vendorId, productId: device.productId });
    return { info, device };
  }
  function buildSteps(profileId) {
    const profile = profileById(profileId);
    return [
      { title: 'Confira seu aparelho', intro: 'Confirme a marca antes de continuar. O nome USB pode ser genérico e não comprova o modelo exato.', steps: [], note: 'Este site apenas lê descritores USB. A identificação funciona sem ativar opções do desenvolvedor e sem depuração USB.' },
      { title: 'Ative as opções do desenvolvedor', path: profile.developerPath, steps: ['No próprio celular, abra o caminho acima. Se os nomes forem diferentes, use a busca das Configurações e confira o guia oficial da marca.', profile.id === 'xiaomi' ? 'Toque sete vezes em Versão do SO (HyperOS) ou Versão MIUI, conforme o sistema do seu celular.' : 'Toque sete vezes em Número de compilação / Número da versão, conforme o nome exibido no aparelho.', 'Se o Android solicitar PIN, padrão ou senha, confirme somente na tela do celular. Nunca informe esses dados neste site.', 'Ao aparecer a confirmação de modo desenvolvedor, volte à tela principal das Configurações.'], note: profile.variant },
      { title: 'Ative a depuração USB, se precisar', path: profile.debugPath, steps: ['Abra Opções do desenvolvedor no próprio aparelho e localize Depuração USB. Ative somente para uma tarefa de depuração em um computador confiável e leia o aviso do Android.', 'Este identificador do site não usa ADB e não solicita autorização RSA. Ativar depuração não é necessário para a identificação feita aqui.', 'Se depois você conectar uma ferramenta de depuração confiável, o Android pode pedir autorização RSA. Confira o computador e a finalidade antes de autorizar. Não marque “Sempre permitir” em computadores compartilhados.', 'Se o controle estiver indisponível ou houver restrição de empresa / administrador, consulte o suporte responsável. Não tente remover a política.'], note: 'Depuração USB e “permitir apps de fontes desconhecidas” são configurações diferentes. Este guia não instala APKs e não altera essas permissões.' },
      { title: 'Encerre e confira no celular', steps: ['Ao terminar a tarefa, volte a Opções do desenvolvedor e desligue Depuração USB.', 'Se aparecer “Revogar autorizações de depuração USB”, use essa opção para remover computadores anteriormente autorizados quando necessário.', 'Desligue também o interruptor principal de Opções do desenvolvedor e confira o estado final no próprio aparelho.', 'Desconecte o cabo. Se quiser, use “Esquecer autorização USB” nesta página, quando disponível, ou remova o acesso USB deste site nas permissões do navegador.'], note: 'Fechar esta janela ou desconectar o cabo não desliga opções do Android. O site não detecta nem confirma esses interruptores; a conferência é manual.' }
    ];
  }
  async function copyHash(controls, env) {
    if (controls.button) controls.button.disabled = true;
    try {
      if (!env.navigator || !env.navigator.clipboard || typeof env.navigator.clipboard.writeText !== 'function') throw new Error('unavailable');
      await env.navigator.clipboard.writeText(APK_SHA256);
      if (controls.status) controls.status.textContent = 'SHA-256 completo copiado: 64 caracteres.';
      return true;
    } catch (_) {
      if (controls.full) controls.full.open = true;
      if (controls.status) controls.status.textContent = 'Não foi possível copiar automaticamente. Abra o hash completo abaixo para selecionar e copiar manualmente.';
      return false;
    } finally { if (controls.button) controls.button.disabled = false; }
  }
  function bindHashControls(document, env) {
    if (!document || typeof document.querySelectorAll !== 'function') return;
    document.querySelectorAll('[data-aadx-hash]').forEach(group => {
      if (group.dataset.aadxHashBound === 'true') return;
      const button = group.querySelector('[data-aadx-hash-copy]'); if (!button) return;
      group.dataset.aadxHashBound = 'true'; button.hidden = false;
      button.addEventListener('click', () => copyHash({ button, full: group.querySelector('[data-aadx-hash-full]'), status: group.querySelector('[data-aadx-hash-status]') }, env));
    });
  }
  function init(document, env) {
    bindHashControls(document, env);
    const form = document.getElementById('adx-device-form'), dialog = document.getElementById('adx-device-dialog');
    if (!form || !dialog || form.dataset.adxReady === 'true') return;
    form.dataset.adxReady = 'true';
    const $ = id => document.getElementById(id);
    const query = $('adx-device-search'), brand = $('adx-device-brand'), model = $('adx-device-model'), usbButton = $('adx-device-usb'), status = $('adx-device-status');
    const body = $('adx-guide-body'), title = $('adx-guide-title'), note = $('adx-guide-note'), source = $('adx-guide-source'), label = $('adx-guide-step-label');
    const back = $('adx-guide-back'), next = $('adx-guide-next'), copy = $('adx-guide-copy'), closeButton = $('adx-guide-close'), copyStatus = $('adx-guide-copy-status');
    const manualButton = $('adx-device-manual'), forgetButton = $('adx-device-forget');
    let profileId = 'generic', confirmedBrand = false, confirmedModel = '', usbInfo = null, selectedDevice = null, selectionSession = 0, step = 0, usbBusy = false, generation = 0, opener = null, fallbackText = null;
    const clear = node => { while (node.firstChild) node.removeChild(node.firstChild); };
    function add(parent, tag, value, className) { const node = document.createElement(tag); if (value !== undefined) node.textContent = value; if (className) node.className = className; parent.appendChild(node); return node; }
    function announce(message) { status.textContent = message; }
    function focus(node) { if (node && typeof node.focus === 'function') node.focus(); }
    function closeGuide() {
      if (typeof dialog.close === 'function') dialog.close(); else dialog.removeAttribute('open');
      focus(opener);
    }
    function summary(parent, name, value, className) { const row = add(parent, 'div', undefined, 'adx-device-summary__row'); add(row, 'dt', name); return add(row, 'dd', value, className); }
    function render() {
      const profile = profileById(profileId), current = buildSteps(profileId)[step];
      clear(body); dialog.dataset.step = String(step + 1); title.textContent = current.title;
      label.textContent = 'Etapa ' + (step + 1) + ' de 4'; note.textContent = current.note;
      if (fallbackText) fallbackText.hidden = true;
      source.href = profile.source; source.textContent = profile.sourceLabel + ' · abre em outra aba'; source.target = '_blank'; source.rel = 'noopener noreferrer';
      back.disabled = step === 0; next.textContent = step === 3 ? 'Concluir' : step === 0 ? 'Confirmar e continuar' : 'Próximo';
      copyStatus.textContent = '';
      if (step === 0) {
        add(body, 'p', current.intro);
        const details = add(body, 'dl', undefined, 'adx-device-summary');
        if (usbInfo) {
          summary(details, 'Fabricante informado pelo USB', usbInfo.manufacturerName || 'Não informado');
          summary(details, 'Nome do produto informado pelo USB', usbInfo.productName || 'Não informado; confirme o modelo abaixo');
          summary(details, 'Identificador USB VID:PID', usbInfo.identifier + ' — código não exclusivo, não é número de série', 'adx-device-identity');
          add(body, 'p', usbInfo.evidence === 'vendor' ? 'A marca sugerida veio do código de fabricante USB. Confirme antes de continuar.' : usbInfo.conflict ? 'Os descritores indicam informações conflitantes. Escolha a marca manualmente.' : 'Os descritores são informações fornecidas pelo aparelho e podem ser genéricos.', 'adx-guide-callout');
        } else summary(details, 'Identificação', 'Guia escolhido manualmente; nenhuma consulta USB foi concluída.');
        const fields = add(body, 'div', undefined, 'adx-guide-confirm');
        const brandLabel = add(fields, 'label', 'Confirme a marca', 'adx-guide-field');
        const select = add(brandLabel, 'select'); select.id = 'adx-guide-brand';
        const placeholder = add(select, 'option', 'Escolha a marca / guia geral'); placeholder.value = '';
        profiles.forEach(item => { const option = add(select, 'option', item.label); option.value = item.id; });
        select.value = confirmedBrand || profileId !== 'generic' ? profileId : '';
        select.addEventListener('change', () => { profileId = profileById(select.value).id; confirmedBrand = Boolean(select.value); next.disabled = !confirmedBrand; source.href = profileById(profileId).source; source.textContent = profileById(profileId).sourceLabel + ' · abre em outra aba'; });
        const modelLabel = add(fields, 'label', 'Modelo (opcional, confira em Sobre o telefone)', 'adx-guide-field');
        const input = add(modelLabel, 'input'); input.id = 'adx-guide-model'; input.type = 'text'; input.maxLength = 120; input.autocomplete = 'off'; input.placeholder = 'Digite se souber; o USB pode não informar'; input.value = confirmedModel;
        input.addEventListener('input', () => { confirmedModel = text(input.value, 120); });
        next.disabled = !select.value;
      } else {
        next.disabled = false;
        if (confirmedModel) add(body, 'p', 'Modelo informado por você: ' + confirmedModel + '. O guia é da marca e varia conforme a versão do sistema.', 'adx-guide-callout');
        if (current.path) add(body, 'p', current.path, 'adx-guide-path');
        const list = add(body, 'ol', undefined, 'adx-guide-steps'); current.steps.forEach(value => add(list, 'li', value));
        if (profile.additionalSources && step === 1) { const links = add(body, 'ul', undefined, 'adx-guide-source-list'); profile.additionalSources.forEach(item => { const link = add(add(links, 'li'), 'a', item.label); link.href = item.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; }); }
        if (step === 3) { const links = add(body, 'ul', undefined, 'adx-guide-source-list'); const link = add(add(links, 'li'), 'a', 'Guia oficial Chrome: revogar autorizações de depuração'); link.href = 'https://developer.chrome.com/docs/devtools/remote-debugging'; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
      }
      title.setAttribute('tabindex', '-1'); focus(title);
    }
    function openGuide(trigger) {
      step = 0; opener = trigger || document.activeElement;
      function readWithoutDialog() {
        const fallback = $('adx-guide-fallback');
        announce('As janelas guiadas não estão disponíveis neste navegador. O guia de leitura abaixo contém os caminhos e as etapas para seguir no próprio celular.');
        if (fallback) { fallback.open = true; fallback.setAttribute('tabindex', '-1'); focus(fallback); if (typeof fallback.scrollIntoView === 'function') fallback.scrollIntoView({ block: 'start', behavior: 'auto' }); }
        return false;
      }
      if (typeof dialog.showModal !== 'function') return readWithoutDialog();
      try { if (!dialog.open) dialog.showModal(); } catch (_) { return readWithoutDialog(); }
      render(); focus(title); return true;
    }
    function openManual(trigger) {
      generation += 1; usbInfo = null;
      const found = search(query.value), explicit = brand.value && brand.value !== 'auto';
      if (found.confidence === 'unsupported' && !explicit) { announce('Este aparelho usa iOS / iPadOS. O guia é para Android. Para consultar um aparelho Android, confirme sua marca na lista.'); focus(brand); return false; }
      profileId = explicit ? profileById(brand.value).id : found.profileId || 'generic'; confirmedBrand = Boolean(explicit); confirmedModel = text(model.value, 120);
      announce(found.confidence === 'brand' && !explicit ? 'Guia da marca sugerido pela busca. Confirme a marca na primeira etapa; o modelo exato não foi verificado.' : 'Guia manual disponível. Confirme a marca e siga as etapas no próprio celular.');
      return openGuide(trigger);
    }
    form.addEventListener('submit', event => { event.preventDefault(); openManual(document.activeElement); });
    if (manualButton) manualButton.addEventListener('click', () => openManual(manualButton));
    usbButton.addEventListener('click', async () => {
      if (usbBusy) return;
      const readiness = getUsbReadiness(env, document);
      if (!readiness.ready) { announce(readiness.message); if (manualButton) manualButton.hidden = false; return; }
      const current = ++generation; usbBusy = true; usbButton.disabled = true;
      announce('Solicitando ao navegador a seleção USB. Se ele mostrar um seletor, escolha somente seu celular Android. A consulta não altera o aparelho.');
      try {
        const identified = await requestUsbIdentification(env, document);
        selectedDevice = identified.device; selectionSession += 1;
        if (forgetButton) forgetButton.hidden = typeof selectedDevice.forget !== 'function';
        if (current !== generation) return;
        usbInfo = identified.info;
        if (usbInfo.evidence === 'unsupported') { announce('O dispositivo informa identidade Apple. Este guia é exclusivo para Android. Confira o celular selecionado ou escolha uma marca Android manualmente.'); return; }
        profileId = usbInfo.profileId; confirmedBrand = false; confirmedModel = text(model.value, 120);
        announce('Descritores USB consultados. Confirme a marca no guia; o modelo exato e as configurações do celular não foram verificados.'); openGuide(usbButton);
      } catch (error) { if (current === generation) announce(formatUsbError(error).message); }
      finally { usbBusy = false; usbButton.disabled = false; if (manualButton) manualButton.hidden = false; }
    });
    if (forgetButton) forgetButton.addEventListener('click', async () => {
      if (!selectedDevice || typeof selectedDevice.forget !== 'function') return;
      const forgottenDevice = selectedDevice, forgottenSession = selectionSession, forgottenGeneration = generation;
      forgetButton.disabled = true;
      try { await forgottenDevice.forget(); if (selectedDevice === forgottenDevice && selectionSession === forgottenSession) { selectedDevice = null; forgetButton.hidden = true; if (generation === forgottenGeneration) announce('A autorização USB do dispositivo solicitado foi esquecida pelo navegador. Nenhuma configuração do Android foi alterada.'); } }
      catch (_) { if (selectionSession === forgottenSession && generation === forgottenGeneration) announce('Não foi possível esquecer a autorização. Remova o acesso USB nas permissões deste site no navegador.'); }
      finally { forgetButton.disabled = false; }
    });
    back.addEventListener('click', () => { if (step > 0) { step -= 1; render(); } });
    next.addEventListener('click', () => {
      if (step === 0) { const select = $('adx-guide-brand'); if (!select || !select.value) { copyStatus.textContent = 'Escolha a marca ou o guia geral para continuar.'; focus(select); return; } profileId = profileById(select.value).id; confirmedBrand = true; const input = $('adx-guide-model'); confirmedModel = text(input && input.value, 120); }
      if (step === 3) closeGuide(); else { step += 1; render(); }
    });
    closeButton.addEventListener('click', closeGuide);
    dialog.addEventListener('cancel', event => { event.preventDefault(); closeGuide(); });
    dialog.addEventListener('close', () => focus(opener));
    dialog.addEventListener('click', event => { if (event.target !== dialog) return; const r = typeof dialog.getBoundingClientRect === 'function' ? dialog.getBoundingClientRect() : null; if (!r || event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) closeGuide(); });
    copy.addEventListener('click', async () => {
      const profile = profileById(profileId);
      const guide = 'AliançaDX — guia manual Android\n' + profile.label + (confirmedModel ? '\nModelo informado por você: ' + confirmedModel : '\nModelo exato não confirmado') + '\n\n' + buildSteps(profileId).map((item, index) => (index + 1) + '. ' + item.title + '\n' + (item.path ? item.path + '\n' : '') + item.steps.map((value, i) => '  ' + (i + 1) + ') ' + value).join('\n') + '\n' + item.note).join('\n\n') + '\n\nFonte: ' + profile.source;
      try { if (!env.navigator || !env.navigator.clipboard || typeof env.navigator.clipboard.writeText !== 'function') throw new Error('unavailable'); await env.navigator.clipboard.writeText(guide); copyStatus.textContent = 'Passo a passo copiado. As alterações são feitas manualmente no celular.'; if (fallbackText) fallbackText.hidden = true; }
      catch (_) { if (!fallbackText) { fallbackText = add(dialog, 'textarea'); fallbackText.className = 'adx-guide-copy-fallback'; fallbackText.readOnly = true; fallbackText.setAttribute('aria-label', 'Passo a passo para copiar manualmente'); } fallbackText.value = guide; fallbackText.hidden = false; focus(fallbackText); if (typeof fallbackText.select === 'function') fallbackText.select(); copyStatus.textContent = 'A cópia automática não está disponível. Selecione o texto abaixo para copiar manualmente.'; }
    });
    document.querySelectorAll('[data-adx-js]').forEach(node => { node.hidden = false; });
    const readiness = getUsbReadiness(env, document); announce(readiness.message);
    return { openManual, close: closeGuide, getState: () => ({ profileId, confirmedBrand, confirmedModel, usbInfo: usbInfo ? { ...usbInfo } : null, step, usbBusy }) };
  }
  return { APK_SHA256, profiles, normalize, search, USBtoGuide, getUsbReadiness, formatUsbError, requestUsbIdentification, buildSteps, copyHash, bindHashControls, init };
});
