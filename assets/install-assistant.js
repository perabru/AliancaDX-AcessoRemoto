/* AliançaDX — orientação local de instalação Android. Sem ADB ou alterações no aparelho. */
(function (global, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (global && global.document) {
    global.AliancaDXInstall = api;
    const start = () => api.init(global.document, global);
    if (global.document.readyState === 'loading') global.document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const PIXEL_SOURCE = 'https://support.google.com/pixelphone/answer/7391672?hl=pt-BR';
  const profiles = [
    { id: 'samsung', label: 'Samsung · Galaxy', aliases: ['samsung', 'galaxy'],
      pattern: /\bsm\s?[a-z]\d{3,}[a-z0-9]*\b/,
      path: 'Configurações → Segurança e privacidade → Mais configurações de segurança → Instalar apps desconhecidos',
      variant: 'Em versões anteriores da One UI, procure também “Biometria e segurança”. Os nomes mudam com a versão do sistema.',
      source: 'https://www.samsung.com/ae/support/mobile-devices/how-to-enable-permission-to-install-apps-from-unknown-source-on-my-samsung-phone/', sourceLabel: 'Guia oficial Samsung' },
    { id: 'xiaomi', label: 'Xiaomi · Redmi · POCO', aliases: ['xiaomi', 'redmi', 'poco', 'miui', 'hyperos'], pattern: /\bmi\s?\d{1,2}\b/,
      path: 'Configurações → Proteção de privacidade → Permissões especiais → Instalar apps desconhecidos',
      variant: 'Em versões anteriores, o caminho pode estar em “Configurações adicionais → Privacidade → Acesso especial”. Use a busca das Configurações se os menus forem diferentes.',
      source: 'https://www.mi.com/global/support/article/KA-33144/', sourceLabel: 'Guia oficial Xiaomi' },
    { id: 'motorola', label: 'Motorola · Moto', aliases: ['motorola', 'moto'], pattern: /\bxt\s?\d{4}[a-z0-9]*\b/,
      path: 'Configurações → Apps → Acesso especial a apps → Instalar apps desconhecidos',
      variant: 'Em versões anteriores, abra “Apps e notificações → Avançadas → Acesso especial a apps”. Confirme os nomes no seu sistema.',
      source: 'https://pt-br.support.motorola.com/ci/fattach/get/3713057/1533921889/redirect/1/filename/moto%2Be5%2Bplay.UG.pt-BR.SSC8C35098A.pdf', sourceLabel: 'Manual oficial Motorola (Android 8, PDF)' },
    { id: 'pixel', label: 'Google · Pixel', aliases: ['google', 'pixel'],
      path: 'Configurações → Apps → Acesso especial a apps → Instalar apps desconhecidos',
      variant: 'Na mensagem exibida ao abrir o APK, “Configurações” também pode levar diretamente à permissão do aplicativo de origem.',
      source: PIXEL_SOURCE, sourceLabel: 'Guia oficial Google Pixel' },
    { id: 'generic', label: 'Outra marca · Guia geral', aliases: ['oppo', 'realme', 'vivo', 'asus', 'zenfone', 'rog phone', 'sony', 'xperia', 'huawei', 'honor', 'nokia', 'nothing', 'infinix', 'tecno', 'android'],
      path: 'Configurações → busque “Instalar apps desconhecidos” → escolha o aplicativo que abre o APK',
      variant: 'Guia geral para Android 8 ou superior. Não é um caminho específico desse modelo. Algumas marcas usam “Acesso especial” ou “Permissões especiais”.',
      source: PIXEL_SOURCE, sourceLabel: 'Referência geral do Android (Google)' }
  ];
  const sourceNames = { chrome: 'Chrome', samsung: 'Samsung Internet', files: 'Arquivos / Meus Arquivos', whatsapp: 'WhatsApp', unknown: 'o aplicativo que realmente abriu o APK' };
  // 0x18d1 é compartilhado por vários aparelhos Android e não comprova um Pixel.
  const vendorProfiles = { 0x04e8: 'samsung', 0x2717: 'xiaomi', 0x22b8: 'motorola' };
  function normalize(value) {
    return String(value == null ? '' : value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().replace(/\s+/g, ' ');
  }
  function profileById(id) { return profiles.find(profile => profile.id === id) || profiles[profiles.length - 1]; }
  function search(value) {
    const query = normalize(value);
    if (!query) return { query, matches: [], profileId: 'generic', confidence: 'none' };
    if (/\b(?:i\s?phone[a-z0-9]*|ipad[a-z0-9]*|ios|apple)\b/.test(query)) return { query, matches: [], profileId: null, confidence: 'unsupported', platform: 'ios' };
    const matches = profiles.filter(profile => profile.aliases.some(alias => {
      const a = normalize(alias);
      return (' ' + query + ' ').includes(' ' + a + ' ') || (a.length >= 4 && query.replace(/\s/g, '').startsWith(a.replace(/\s/g, '')));
    }) || (profile.pattern && profile.pattern.test(query)));
    const specific = matches.filter(profile => profile.id !== 'generic');
    const selected = specific.length === 1 ? specific[0] : (!specific.length && matches.length ? profileById('generic') : null);
    return { query, matches: (specific.length ? specific : matches).map(profile => ({ id: profile.id, label: profile.label })), profileId: selected ? selected.id : 'generic', confidence: selected ? (selected.id === 'generic' ? 'generic' : 'brand') : (specific.length > 1 ? 'ambiguous' : 'none') };
  }
  function cleanDescriptor(value) { return String(value || '').replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, 120); }
  function USBtoGuide(info) {
    const manufacturerName = cleanDescriptor(info && info.manufacturerName);
    const productName = cleanDescriptor(info && info.productName);
    const vendorId = info && Number.isInteger(info.vendorId) && info.vendorId >= 0 && info.vendorId <= 65535 ? info.vendorId : null;
    const productId = info && Number.isInteger(info.productId) && info.productId >= 0 && info.productId <= 65535 ? info.productId : null;
    const byName = search(manufacturerName + ' ' + productName);
    if (byName.confidence === 'unsupported' || vendorId === 0x05ac) return { manufacturerName, productName, vendorId, productId, profileId: null, conflict: false, evidence: 'unsupported' };
    const byVendor = vendorProfiles[vendorId];
    let named = byName.confidence === 'brand' ? byName.profileId : null;
    if (named === 'pixel' && !/\bpixel[a-z0-9]*\b/.test(normalize(productName))) named = null;
    const descriptors = ' ' + normalize(manufacturerName + ' ' + productName) + ' ';
    const otherMaker = profileById('generic').aliases.some(alias => alias !== 'android' && descriptors.includes(' ' + normalize(alias) + ' '));
    const conflict = Boolean(named && byVendor && named !== byVendor) || Boolean(otherMaker && byVendor) || byName.confidence === 'ambiguous';
    const profileId = conflict ? 'generic' : (named || byVendor || 'generic');
    return { manufacturerName, productName, vendorId, productId, profileId, conflict, evidence: conflict ? 'conflict' : (named ? 'descriptor' : (byVendor ? 'vendor' : 'unknown')) };
  }
  function usbErrorMessage(error) {
    const name = error && error.name;
    if (name === 'NotFoundError') return 'Nenhum dispositivo foi selecionado. Você pode tentar de novo ou buscar a marca e o modelo acima.';
    if (name === 'SecurityError' || name === 'NotAllowedError') return 'O navegador não autorizou o acesso USB. Use a busca manual; nenhuma configuração do celular foi alterada.';
    if (name === 'NetworkError') return 'Não foi possível consultar esse dispositivo USB. Confira o cabo de dados ou use a busca manual.';
    return 'Não foi possível obter as informações USB. Use a busca por marca ou escolha o guia geral. Nenhuma configuração do celular foi alterada.';
  }
  async function requestUsbIdentification(env) {
    const nav = env && env.navigator;
    if (!env || !env.isSecureContext) { const error = new Error('HTTPS required'); error.name = 'SecurityError'; throw error; }
    if (!nav || !nav.usb || typeof nav.usb.requestDevice !== 'function') { const error = new Error('WebUSB unavailable'); error.name = 'NotSupportedError'; throw error; }
    const device = await nav.usb.requestDevice({ filters: [] });
    const info = USBtoGuide({ manufacturerName: device.manufacturerName, productName: device.productName, vendorId: device.vendorId, productId: device.productId });
    return { device, info };
  }
  function makeSteps(profileId, sourceId) {
    const profile = profileById(profileId);
    const source = sourceNames[sourceId] || sourceNames.unknown;
    return [
      'Abra o APK obtido pelo canal do projeto. Se aparecer a mensagem de instalação bloqueada pela origem, toque em “Configurações”.',
      'Se precisar procurar manualmente: ' + profile.path + '.',
      'Na lista de origens, escolha ' + source + '. Autorize “Permitir desta fonte” somente para esse aplicativo. Não procure AliançaDX nessa lista.',
      'Volte ao APK e confirme a instalação no celular. Um aviso do Play Protect exige avaliação própria; esta permissão não o remove.',
      'Depois da instalação, volte à mesma tela e desative a permissão para a origem. Isso mantém o AliançaDX instalado.'
    ];
  }
  function init(document, env) {
    const root = document.getElementById('instalar-android');
    if (!root || root.dataset.aadxReady === 'true') return;
    root.dataset.aadxReady = 'true';
    const $ = selector => root.querySelector(selector);
    const query = $('#aadx-model');
    const brand = $('#aadx-brand');
    const source = $('#aadx-source');
    const result = $('#aadx-search-status');
    const guideTitle = $('#aadx-guide-title');
    const guide = $('#aadx-guide');
    const guideShortcut = $('#aadx-view-guide');
    const guideNote = $('#aadx-guide-note');
    const path = $('#aadx-guide-path');
    const steps = $('#aadx-guide-steps');
    const sourceLink = $('#aadx-guide-source');
    const copyStatus = $('#aadx-copy-status');
    const copyFallback = $('#aadx-copy-fallback');
    const usbButton = $('#aadx-usb');
    const usbStatus = $('#aadx-usb-status');
    const usbInfo = $('#aadx-usb-info');
    const usbName = $('#aadx-usb-name');
    const usbMaker = $('#aadx-usb-maker');
    const usbIdentity = $('#aadx-usb-identity');
    const forgetButton = $('#aadx-usb-forget');
    const nav = env.navigator || {};
    let currentProfile = 'generic';
    let selectedDevice = null;
    let usbBusy = false;
    function announce(text) { result.textContent = text; }
    function render(id) {
      const profile = profileById(id);
      guide.hidden = false;
      guideShortcut.hidden = false;
      currentProfile = profile.id;
      brand.value = profile.id;
      guideTitle.textContent = profile.label;
      guideNote.textContent = profile.variant;
      path.textContent = profile.path;
      steps.replaceChildren();
      makeSteps(profile.id, source.value).forEach(text => { const item = document.createElement('li'); item.textContent = text; steps.appendChild(item); });
      sourceLink.href = profile.source;
      sourceLink.textContent = profile.sourceLabel + ' · abre em outra aba';
      copyStatus.textContent = '';
      copyFallback.hidden = true;
    }
    root.querySelectorAll('[data-aadx-js]').forEach(element => { element.hidden = false; });
    $('#aadx-search-form').addEventListener('submit', event => {
      event.preventDefault();
      const found = search(query.value);
      if (found.confidence === 'unsupported') {
        guide.hidden = true;
        guideShortcut.hidden = true;
        brand.value = '';
        announce('Esse aparelho usa iOS ou iPadOS. O arquivo APK é exclusivo para Android e não pode ser instalado em iPhone ou iPad. Para mostrar o tutorial Android, escolha uma marca Android na lista.');
        return;
      }
      render(found.profileId);
      if (found.confidence === 'brand') announce('Encontramos o guia da marca: ' + profileById(found.profileId).label + '. Confirme a marca abaixo. O caminho depende da versão do Android.');
      else if (found.confidence === 'ambiguous') announce('Sua busca combina mais de uma marca. Escolha a marca na lista para mostrar o guia correto.');
      else if (found.confidence === 'generic') announce('Para essa marca, mostramos o guia geral do Android. Os nomes dos menus podem ser diferentes.');
      else announce(found.query ? 'Não reconhecemos essa marca ou esse código de modelo. Escolha a marca na lista ou siga o guia geral abaixo.' : 'Digite uma marca ou modelo, ou escolha a marca na lista. O guia geral já está disponível abaixo.');
    });
    brand.addEventListener('change', () => { render(brand.value); announce('Guia selecionado: ' + profileById(brand.value).label + '.'); });
    source.addEventListener('change', () => { if (!guide.hidden) render(currentProfile); });
    root.querySelectorAll('[data-aadx-brand]').forEach(button => button.addEventListener('click', () => { query.value = button.textContent.trim(); render(button.dataset.aadxBrand); announce('Guia selecionado: ' + profileById(button.dataset.aadxBrand).label + '.'); }));
    $('#aadx-clear').addEventListener('click', () => { query.value = ''; render('generic'); announce('Busca limpa. O guia geral está disponível abaixo.'); query.focus(); });
    $('#aadx-copy').addEventListener('click', async () => {
      const text = 'AliançaDX — instalação no Android 8 ou superior\n' + profileById(currentProfile).label + '\n\n' + makeSteps(currentProfile, source.value).map((step, index) => (index + 1) + '. ' + step).join('\n\n') + '\n\n' + profileById(currentProfile).source;
      try {
        if (!nav.clipboard || typeof nav.clipboard.writeText !== 'function') throw new Error('clipboard-unavailable');
        await nav.clipboard.writeText(text);
        copyStatus.textContent = 'Passo a passo copiado.';
      } catch (_) {
        copyFallback.value = text;
        copyFallback.hidden = false;
        copyFallback.focus();
        copyFallback.select();
        copyStatus.textContent = 'A cópia automática não está disponível. O texto abaixo está selecionado para você copiar.';
      }
    });
    const usbAvailable = Boolean(env.isSecureContext && nav.usb && typeof nav.usb.requestDevice === 'function');
    if (!usbAvailable) {
      usbButton.disabled = true;
      usbStatus.textContent = !env.isSecureContext ? 'A identificação USB exige uma página HTTPS. A busca e o tutorial continuam disponíveis.' : 'Este navegador não oferece WebUSB. No computador, tente Chrome ou Edge; você também pode usar a busca manual acima.';
    }
    usbButton.addEventListener('click', async () => {
      if (!usbAvailable || usbBusy) return;
      usbBusy = true;
      usbButton.disabled = true;
      forgetButton.disabled = true;
      usbStatus.textContent = 'Selecione somente o celular Android na janela do navegador. Se ele não aparecer, feche a janela e use a busca manual.';
      try {
        const identified = await requestUsbIdentification(env);
        const device = identified.device;
        selectedDevice = device;
        const info = identified.info;
        usbName.textContent = info.productName || 'Nome não informado pelo dispositivo';
        usbMaker.textContent = info.manufacturerName || 'Fabricante não informado pelo dispositivo';
        const hex = value => value == null ? 'não informado' : value.toString(16).padStart(4, '0').toUpperCase();
        usbIdentity.textContent = 'Fabricante USB: ' + hex(info.vendorId) + ' · Produto USB: ' + hex(info.productId);
        usbInfo.hidden = false;
        forgetButton.hidden = typeof device.forget !== 'function';
        if (info.evidence === 'unsupported') {
          guide.hidden = true;
          guideShortcut.hidden = true;
          brand.value = '';
          usbStatus.textContent = 'O dispositivo informa uma identidade Apple. Este APK é exclusivo para Android e não pode ser instalado em iPhone ou iPad. Se você conectou outro aparelho, confirme a seleção no navegador.';
          announce('Consulta concluída. Não foi sugerido um guia de instalação Android para esse dispositivo.');
          return;
        }
        render(info.profileId);
        if (info.evidence === 'conflict') usbStatus.textContent = 'As informações USB não indicam uma única marca. Confira o modelo em Configurações → Sobre o telefone e escolha a marca acima.';
        else if (info.evidence === 'unknown') usbStatus.textContent = 'O dispositivo foi selecionado, mas seus descritores não identificam uma marca com guia específico. Confira o nome acima e escolha a marca manualmente.';
        else usbStatus.textContent = 'Sugestão de guia: ' + profileById(info.profileId).label + '. O nome e o fabricante acima são os informados pelo USB, sem confirmação do modelo exato. Confira a marca antes de seguir o guia.';
        announce('Consulta USB concluída. Confirme a marca e siga o guia abaixo no próprio celular.');
      } catch (error) { usbStatus.textContent = usbErrorMessage(error); }
      finally { usbBusy = false; usbButton.disabled = false; forgetButton.disabled = false; }
    });
    forgetButton.addEventListener('click', async () => {
      if (usbBusy || !selectedDevice || typeof selectedDevice.forget !== 'function') return;
      const deviceToForget = selectedDevice;
      usbBusy = true;
      usbButton.disabled = true;
      forgetButton.disabled = true;
      try {
        await deviceToForget.forget();
        if (selectedDevice === deviceToForget) {
          selectedDevice = null;
          usbInfo.hidden = true;
          forgetButton.hidden = true;
        }
        usbStatus.textContent = 'A autorização USB deste dispositivo foi esquecida pelo navegador. O tutorial continua disponível.';
      } catch (_) { usbStatus.textContent = 'Não foi possível esquecer a autorização agora. Você pode removê-la nas permissões USB deste site no navegador.'; }
      finally { usbBusy = false; usbButton.disabled = !usbAvailable; forgetButton.disabled = false; }
    });
    if (nav.usb && typeof nav.usb.addEventListener === 'function') nav.usb.addEventListener('disconnect', event => {
      if (selectedDevice && event.device === selectedDevice) usbStatus.textContent = 'O cabo do dispositivo consultado foi desconectado. O guia permanece disponível; a permissão de instalação é alterada somente no celular.';
    });
    render('generic');
  }
  return { normalize, search, USBtoGuide, usbErrorMessage, requestUsbIdentification, makeSteps, profiles, init };
});
