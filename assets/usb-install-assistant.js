/* Instalação USB AliançaDX: APK fixado, confirmação explícita e progresso local. */
(function (global, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (global && global.document) {
    global.AliancaDXUsbInstall = api;
    const start = () => api.init(global.document, global);
    if (global.document.readyState === 'loading') global.document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const APK = Object.freeze({ size: 34699638, sha256: '3ff081e7c6244be6c07d5a45d658f05c845d052bbc7064deec8b736e89379f3c', packageName: 'br.com.perabru.minianydesk.controlador', versionCode: 62000 });
  function issue(code, message) { const error = new Error(message); error.code = code; return error; }
  function safeText(value, limit) {
    return String(value == null ? '' : value)
      .replace(/-----BEGIN[\s\S]*?-----END[^-]*-----/g, '[chave omitida]')
      .replace(/(?:privateKey|private_key|publicKey|public_key|rsaKey|rsa_key)\s*["']?\s*[:=][^\r\n]*/gi, '[chave omitida]')
      .replace(/\b(?:serialNumber|serial_number|deviceSerial|device_serial|serial|pin|password|passcode|senha|token|secret)\s*["']?\s*[:=]\s*["']?[^\s,;\r\n]+/gi, '[dado omitido]')
      .replace(/\bPIN\s+\d{4,12}\b/gi, '[PIN omitido]')
      .replace(/\bAuthorization\s*:\s*[^\r\n]*/gi, '[autorização omitida]')
      .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '').slice(0, limit || 900);
  }
  async function copyHash(controls, env) {
    const status = controls && controls.status, full = controls && controls.full, button = controls && controls.button;
    if (button) button.disabled = true;
    try {
      const clipboard = env && env.navigator && env.navigator.clipboard;
      if (!clipboard || typeof clipboard.writeText !== 'function') throw new Error('Clipboard unavailable');
      await clipboard.writeText(APK.sha256);
      if (status) status.textContent = 'SHA-256 completo copiado: 64 caracteres.';
      return true;
    } catch (_) {
      if (full) full.open = true;
      if (status) status.textContent = full ? 'Não foi possível copiar automaticamente. O hash completo foi aberto para você selecionar e copiar manualmente.' : 'Não foi possível copiar automaticamente. Consulte o hash completo publicado nesta página para copiar manualmente.';
      return false;
    } finally { if (button) button.disabled = false; }
  }
  function bindHashControls(document, env) {
    if (!document || typeof document.querySelectorAll !== 'function') return;
    document.querySelectorAll('[data-aadx-hash]').forEach(group => {
      if (group.dataset && group.dataset.aadxHashBound === 'true') return;
      const button = group.querySelector('[data-aadx-hash-copy]');
      if (!button || typeof button.addEventListener !== 'function') return;
      if (group.dataset) group.dataset.aadxHashBound = 'true';
      const controls = { button, full: group.querySelector('[data-aadx-hash-full]'), status: group.querySelector('[data-aadx-hash-status]') };
      button.hidden = false;
      button.addEventListener('click', () => copyHash(controls, env));
    });
  }
  function buildDiagnostic(state, generatedAt) {
    state = state || {};
    const line = value => safeText(value, 1200).replace(/[\r\n]+/g, ' ');
    const when = value => { const date = value instanceof Date ? value : new Date(value); return Number.isFinite(date.getTime()) ? date.toISOString() : 'não informado'; };
    const device = state.device || {}, error = state.error || null;
    const logs = Array.isArray(state.logs) ? state.logs.slice(-100) : [];
    const api = Number(device.apiLevel);
    const abis = Array.isArray(device.abis) ? device.abis.filter(value => typeof value === 'string' && /^[a-z0-9_-]{1,40}$/i.test(value)).join(', ') : '';
    const text = [
      'AliançaDX — diagnóstico local de instalação USB',
      'Gerado em: ' + when(generatedAt || new Date()),
      'Etapa: ' + line(state.stage || 'não informada'),
      'Status: ' + line(state.message || 'não informado'),
      'Operação em andamento: ' + (state.busy ? 'sim' : 'não'),
      'Conexão ativa informada pela aba: ' + (state.connected ? 'sim' : 'não'),
      'APK validado nesta aba: ' + (state.fileInfo && state.fileInfo.valid === true ? 'sim' : 'não'),
      'Instalação confirmada: ' + (state.installVerified ? 'sim' : 'não'),
      'Conferência manual declarada: ' + (state.manualChecked ? 'sim' : 'pendente'),
      'Marca informada pelo aparelho: ' + line(device.manufacturer || 'não informada'),
      'Modelo informado pelo aparelho: ' + line(device.model || 'não informado'),
      'Android API: ' + (Number.isFinite(api) && api > 0 ? api : 'não informada'),
      'Arquiteturas: ' + (abis || 'não informadas')
    ];
    if (state.cleanup) text.push('Opções de desenvolvedor: ' + line(state.cleanup.developerOptions || 'unconfirmed'), 'Depuração USB: ' + line(state.cleanup.usbDebugging || 'unconfirmed'), 'Conferência no celular continua necessária.');
    if (state.closeResult) text.push('Encerramento USB confirmado: ' + (state.closeResult.closed === true ? 'sim' : 'não'), 'Autorização USB do navegador esquecida: ' + (state.closeResult.forgotten === true ? 'sim' : 'não'));
    text.push('', 'Erro atual:');
    if (error) text.push('Código: ' + line(error.code || 'UNKNOWN'), 'Mensagem: ' + line(error.title || ''), 'Detalhe: ' + line(error.detail || ''), 'Ação: ' + line(error.action || ''));
    else text.push('Nenhum erro registrado no estado atual.');
    text.push('', 'Histórico local:');
    logs.forEach(entry => text.push(when(entry && entry.time) + ' [' + line(entry && entry.level || 'info') + '] ' + line(entry && entry.message || '')));
    if (!logs.length) text.push('Nenhuma etapa registrada.');
    // Apenas campos de diagnóstico são selecionados. Blob, caminho, serial, chaves,
    // stack e demais propriedades do cliente ou do aparelho não são serializados.
    return text.join('\n');
  }
  async function copyDiagnostic(state, controls, env) {
    controls = controls || {};
    const text = buildDiagnostic(state);
    if (controls.button) controls.button.disabled = true;
    try {
      const clipboard = env && env.navigator && env.navigator.clipboard;
      if (!clipboard || typeof clipboard.writeText !== 'function') throw new Error('Clipboard unavailable');
      await clipboard.writeText(text);
      if (controls.fallback) controls.fallback.hidden = true;
      if (controls.status) controls.status.textContent = 'Diagnóstico copiado. Revise o texto antes de compartilhar.';
      return true;
    } catch (_) {
      if (controls.fallback) {
        controls.fallback.value = text; controls.fallback.hidden = false;
        if (typeof controls.fallback.focus === 'function') controls.fallback.focus();
        if (typeof controls.fallback.select === 'function') controls.fallback.select();
      }
      if (controls.status) controls.status.textContent = controls.fallback ? 'Não foi possível copiar automaticamente. O diagnóstico abaixo está disponível para você selecionar e copiar manualmente.' : 'Não foi possível copiar automaticamente. Consulte as etapas e os detalhes do erro nesta página.';
      return false;
    } finally { if (controls.button) controls.button.disabled = false; }
  }
  async function validateApk(file, cryptoApi) {
    if (!file || typeof file.arrayBuffer !== 'function') throw issue('NO_APK', 'Selecione o APK baixado pelo link do projeto.');
    if (file.size !== APK.size) throw issue('APK_SIZE', 'O tamanho não corresponde ao APK oficial 6.2.0. Esperado: ' + APK.size + ' bytes. Recebido: ' + Number(file.size || 0) + ' bytes.');
    if (!cryptoApi || !cryptoApi.subtle || typeof cryptoApi.subtle.digest !== 'function') throw issue('CRYPTO_UNAVAILABLE', 'Este navegador não consegue verificar o SHA-256 localmente. Use uma página HTTPS no Chrome ou Edge atualizado.');
    const bytes = await file.arrayBuffer();
    if (!bytes || bytes.byteLength !== APK.size) throw issue('APK_SIZE', 'A leitura do arquivo não corresponde ao tamanho oficial. Baixe novamente o APK.');
    const digest = await cryptoApi.subtle.digest('SHA-256', bytes);
    const sha256 = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    if (sha256 !== APK.sha256) throw issue('APK_HASH', 'O SHA-256 é diferente do APK oficial. A instalação por este assistente foi recusada.');
    const name = safeText(file.name || 'AliancaDX_Acesso_Remoto_6.2.0.apk', 140);
    // Blob/File faz uma cópia imutável dos mesmos bytes que acabaram de ser validados.
    const snapshot = typeof File === 'function' ? new File([bytes], name, { type: 'application/vnd.android.package-archive' }) : new Blob([bytes], { type: 'application/vnd.android.package-archive' });
    return { valid: true, file: snapshot, name, size: APK.size, sha256, packageName: APK.packageName, versionCode: APK.versionCode };
  }
  function formatError(error) {
    const code = String(error && ((typeof error.code === 'string' && error.code) || error.name) || 'UNKNOWN');
    const detail = safeText(error && error.message || 'Não foram fornecidos detalhes adicionais.');
    const text = code + ' ' + detail;
    const androidFailure = /^(?:INSTALL_|VERIFY_|VERIFICATION_)/.test(code) || /INSTALL_FAILED_|INSTALL_PARSE_FAILED_|PARSE_FAILED_/.test(detail);
    let title = 'Não foi possível concluir esta etapa.';
    let action = 'Confira a etapa indicada no histórico. Refaça a conexão somente quando estiver pronto; a instalação não deve ser considerada confirmada sem a verificação final.';
    if (/NO_APK/.test(code)) { title = 'Selecione o APK antes de continuar.'; action = 'Use o link do projeto, salve o arquivo no computador e selecione esse APK.'; }
    else if (/APK_SIZE|APK_HASH|APK_MISMATCH/.test(code)) { title = 'O arquivo não corresponde ao APK autorizado.'; action = 'Baixe novamente pelo link desta página. Não foi iniciada a instalação com esse arquivo.'; }
    else if (/USB_BUSY|DeviceBusyError/.test(code) || (!androidFailure && /already (?:is )?in use|already in used|already (?:being )?used|used by another|in use by another|LIBUSB_ERROR_BUSY/i.test(text))) { title = 'Outro programa ou aba está usando a conexão USB.'; action = 'Encerre a conexão deste celular em outras abas de instalação, Android Studio ou ferramentas de depuração. Depois, desconecte e reconecte o cabo e tente nesta aba. Não altere o Play Protect.'; }
    else if (/CRYPTO_UNAVAILABLE|HTTPS_REQUIRED/.test(code) || code === 'SecurityError' && /https|secure context|insecure context/i.test(detail)) { title = 'A verificação exige um navegador compatível e HTTPS.'; action = 'Abra o endereço HTTPS do site em Chrome ou Edge atualizado no computador. A instalação manual continua disponível.'; }
    else if (!androidFailure && (code === 'SecurityError' || /claimInterface|access denied|unable to claim|failed to claim|protected interface|interface.*denied/i.test(text))) { title = 'O navegador ou o sistema não liberou a interface USB.'; action = 'Confira se escolheu o Android correto, desbloqueie o celular e leia a autorização de depuração RSA. A interface também pode estar ocupada ou indisponível no sistema. Se persistir, use a instalação manual; não desative proteções do celular.'; }
    else if (/SDK_LOAD|UNSUPPORTED|NotSupportedError|SHELL_UNSUPPORTED/.test(code)) { title = 'O assistente USB não está disponível neste ambiente.'; action = 'Use Chrome ou Edge no computador e confira o cabo de dados. Se continuar indisponível, use o tutorial de instalação manual; não troque drivers ou proteções apenas para tentar continuar.'; }
    else if (/CONSENT_REQUIRED/.test(code)) { title = 'Confirme a autorização para usar o aparelho.'; action = 'Leia a preparação e marque a confirmação somente se o aparelho for seu ou você tiver autorização do responsável.'; }
    else if (/NotFoundError|NO_DEVICE/.test(code)) { title = 'Nenhum aparelho foi selecionado.'; action = 'Você pode conectar novamente e escolher seu Android na janela do navegador. Nenhuma instalação foi confirmada.'; }
    else if (/NotAllowedError/.test(code)) { title = 'O navegador não autorizou a conexão.'; action = 'Confira a permissão USB deste site ou utilize o procedimento manual. Não é necessário desativar o Play Protect.'; }
    else if (/AUTH_TIMEOUT|CONNECT_TIMEOUT/.test(code)) { title = 'A conexão ou a autorização no celular demorou além do esperado.'; action = 'Confira o pedido de depuração RSA na tela desbloqueada do celular e o cabo de dados. Aguarde o encerramento indicado no histórico antes de conectar novamente. A instalação ainda não foi confirmada.'; }
    else if (/SYNC_OPEN_TIMEOUT/.test(code)) { title = 'O Android não confirmou a abertura do canal para enviar o APK.'; action = 'O comando de instalação não foi enviado nesta tentativa. Aguarde o encerramento, reconecte o cabo de dados e confira a autorização RSA no celular. Se acontecer novamente, copie o diagnóstico desta aba para investigar.'; }
    else if (/UPLOAD_TIMEOUT/.test(code)) { title = 'A transferência do APK demorou além do esperado.'; action = 'Confira o cabo de dados e o histórico. Verifique no celular se o aplicativo chegou a ser instalado e confira as opções de desenvolvedor antes de tentar novamente.'; }
    else if (/TIMEOUT/.test(code)) { title = 'A etapa demorou além do esperado.'; action = 'Confira a etapa ativa, a tela do celular e o cabo de dados. A instalação e o desligamento das opções de desenvolvedor precisam ser conferidos no aparelho antes de tentar novamente.'; }
    else if (/AbortError|CANCELLED/.test(code)) { title = 'Operação cancelada.'; action = 'Confira no celular se a instalação chegou a terminar. Desative manualmente a depuração USB e as opções de desenvolvedor se a confirmação automática não estiver disponível.'; }
    else if (code === 'INCOMPATIBLE' || /INSTALL_FAILED_NO_MATCHING_ABIS|INSTALL_FAILED_OLDER_SDK/.test(text)) { title = 'O aparelho não atende aos requisitos deste APK.'; action = 'Este APK exige Android 8/API 26 ou superior e sistema de 64 bits arm64-v8a ou x86_64. Use um aparelho compatível.'; }
    else if (/INSTALL_FAILED_UPDATE_INCOMPATIBLE|INSTALL_PARSE_FAILED_INCONSISTENT_CERTIFICATES|signature|assinatura/i.test(text)) { title = 'A assinatura não corresponde ao aplicativo já instalado.'; action = 'Interrompa a tentativa e procure o responsável pela versão instalada. Não desinstale o aplicativo para contornar esse erro: isso pode apagar dados.'; }
    else if (/INSTALL_FAILED_INVALID_APK|INSTALL_PARSE_FAILED|PARSE_FAILED/.test(text)) { title = 'O Android rejeitou a estrutura ou a leitura do APK.'; action = 'Consulte o detalhe informado pelo instalador e baixe novamente o APK pelo link do projeto. Se o arquivo passar pela verificação e o erro persistir, copie o diagnóstico para investigar a compatibilidade; não desative as verificações do Android.'; }
    else if (/INSTALL_FAILED_VERSION_DOWNGRADE/.test(text)) { title = 'Já existe uma versão mais recente no celular.'; action = 'Mantenha a versão mais recente e solicite ao responsável um APK de atualização compatível. O assistente não força downgrade.'; }
    else if (/INSTALL_FAILED_INSUFFICIENT_STORAGE|ENOSPC/.test(text)) { title = 'O Android informou falta de espaço.'; action = 'Libere espaço pelo gerenciamento de armazenamento do próprio celular e tente novamente quando estiver pronto.'; }
    else if (/INSTALL_FAILED_USER_RESTRICTED|USER_RESTRICTED|INSTALL_FAILED_VERIFICATION_FAILURE|blocked|policy|Play Protect/i.test(text)) { title = 'O Android ou uma política do aparelho bloqueou a instalação.'; action = 'Consulte o aviso no celular ou o administrador do dispositivo. Este assistente não desativa o Play Protect nem remove restrições de segurança.'; }
    else if (/NetworkError|DISCONNECTED/.test(code)) { title = 'A conexão USB foi interrompida.'; action = 'Confira no celular se o aplicativo foi instalado. A perda da conexão não confirma que a depuração ou as opções de desenvolvedor foram desligadas; verifique-as manualmente.'; }
    else if (/VERIFY_FAILED|INSTALL_NOT_VERIFIED|VERIFICATION_FAILED/.test(code)) { title = 'A instalação não pôde ser confirmada.'; action = 'Confira no celular o aplicativo e sua versão. O assistente não marcará sucesso sem confirmar pacote e versão esperados.'; }
    else if (/BUSY/.test(code)) { title = 'Outra etapa ainda está em andamento.'; action = 'Aguarde ou cancele a operação atual antes de iniciar outra.'; }
    return { code, title, action, detail };
  }
  function createController(deps) {
    deps = deps || {};
    const emit = typeof deps.onState === 'function' ? deps.onState : function () {};
    const event = typeof deps.onEvent === 'function' ? deps.onEvent : function () {};
    const clock = deps.now || (() => new Date());
    const timers = deps.timers || { setTimeout, clearTimeout };
    const Abort = deps.AbortController || AbortController;
    const state = { stage: 'idle', message: 'Baixe e selecione o APK oficial para começar.', sdkReady: false, validating: false, busy: false, fileInfo: null, device: null, connected: false, installVerified: false, progress: null, error: null, cleanup: null, closeResult: null, manualChecked: false, logs: [] };
    let sdk = null, client = null, activeAbort = null, generation = 0, preparing = null, closingPromise = null;
    function snapshot() { return { ...state, logs: state.logs.slice() }; }
    function publish() { emit(snapshot()); }
    function log(message, level) {
      const item = { time: clock().toISOString(), message: safeText(message, 900), level: level || 'info' };
      state.logs.push(item); if (state.logs.length > 100) state.logs.shift(); event(item); publish();
    }
    function move(stage, message) { state.stage = stage; state.message = message; log(message); }
    function report(error) { const formatted = formatError(error); state.error = formatted; state.stage = formatted.code === 'CANCELLED' || formatted.code === 'AbortError' ? 'cancelled' : 'error'; state.message = formatted.title; log(formatted.title + ' ' + formatted.action, 'error'); log('Detalhe do erro [' + formatted.code + ']: ' + formatted.detail, 'error'); return formatted; }
    function withAbort(promise, signal) {
      if (signal.aborted) return Promise.reject(issue('CANCELLED', 'Cancelamento solicitado.'));
      return new Promise((resolve, reject) => {
        const abort = () => reject(signal.reason && signal.reason.code ? signal.reason : issue('CANCELLED', 'Cancelamento solicitado. O resultado no aparelho precisa ser conferido.'));
        signal.addEventListener('abort', abort, { once: true });
        Promise.resolve(promise).then(value => { signal.removeEventListener('abort', abort); resolve(value); }, error => { signal.removeEventListener('abort', abort); reject(error); });
      });
    }
    function sdkStage(value) {
      if (!value || !value.message) return;
      const stage = String(value.stage || '');
      if (stage === 'disconnected') {
        state.connected = false; state.progress = null;
        if (!state.busy) { state.busy = true; report(issue('DISCONNECTED', value.message)); closeClient().finally(() => { state.busy = false; publish(); }); return; }
      }
      else if (/auth|rsa/i.test(stage)) state.stage = 'authorizing';
      else if (/install|verif/i.test(stage) && state.connected) { state.stage = 'installing'; state.progress = null; }
      state.message = safeText(value.message); log((value.code ? '[' + safeText(value.code, 70) + '] ' : '') + state.message, stage === 'warning' || stage === 'disconnected' ? 'warning' : 'info');
    }
    async function closeClient() {
      if (closingPromise) return closingPromise;
      const closing = client; client = null;
      if (!closing) { state.connected = false; publish(); return; }
      closingPromise = (async () => {
        try {
          state.closeResult = await closing.close({ forget: true });
          state.connected = false;
          log(state.closeResult && state.closeResult.closed ? 'Conexão USB encerrada.' : 'O encerramento da conexão USB não foi confirmado.', state.closeResult && state.closeResult.closed ? 'info' : 'warning');
          log(state.closeResult && state.closeResult.forgotten ? 'Autorização USB deste navegador esquecida. Isso não confirma o estado das opções no Android.' : 'Não foi possível confirmar a remoção da autorização USB do navegador. Confira as permissões USB do site.', 'warning');
          if (state.closeResult && Array.isArray(state.closeResult.errors)) state.closeResult.errors.forEach(error => log('Detalhe do encerramento: ' + safeText(typeof error === 'string' ? error : (error.step || '') + ': ' + (error.message || 'Falha sem detalhes')), 'warning'));
        } catch (error) { state.connected = false; state.closeResult = { closed: false, forgotten: false }; log('Falha ao confirmar o encerramento USB: ' + safeText(error && error.message) + ' Confira manualmente o celular e as permissões USB do navegador.', 'warning'); }
        publish();
      })();
      try { return await closingPromise; } finally { closingPromise = null; }
    }
    async function prepare() {
      if (sdk) return true;
      if (preparing) return preparing;
      move('loading-sdk', 'Preparando os componentes USB locais. Nenhuma conexão será solicitada nesta etapa.');
      preparing = (async () => {
        try { sdk = await deps.loadSdk(); if (!sdk || typeof sdk.createClient !== 'function') throw issue('SDK_LOAD', 'O componente USB não fornece a interface esperada.'); state.sdkReady = true; if (state.error && state.error.code === 'SDK_LOAD') state.error = null; if (state.stage === 'loading-sdk') move(state.fileInfo ? 'ready' : 'idle', 'Componentes USB prontos. Valide o APK e conclua a preparação no celular.'); else log('Componentes USB prontos. A validação do APK e as demais confirmações continuam necessárias.'); return true; }
        catch (error) { sdk = null; state.sdkReady = false; report(issue('SDK_LOAD', error && error.message || 'Falha ao carregar componentes USB.')); return false; }
        finally { preparing = null; publish(); }
      })(); return preparing;
    }
    async function selectFile(file) {
      if (state.busy || client) throw issue('BUSY', 'Encerre a conexão atual antes de trocar o APK.');
      const current = ++generation; state.fileInfo = null; state.device = null; state.progress = null; state.installVerified = false; state.cleanup = null; state.closeResult = null; state.manualChecked = false; state.error = null; state.validating = true;
      move('validating-apk', 'Conferindo tamanho e SHA-256 do APK local. O arquivo não será enviado a um servidor.');
      try {
        const valid = await validateApk(file, deps.crypto);
        if (current !== generation) return null;
        state.fileInfo = valid; move('ready', 'APK 6.2.0 validado. Leia os passos no celular e confirme a autorização antes de conectar.'); return valid;
      } catch (error) { if (current === generation) report(error); return null; }
      finally { if (current === generation) { state.validating = false; publish(); } }
    }
    async function connect(consent) {
      if (state.busy || client || closingPromise) { report(issue('BUSY', 'Já existe uma operação ou conexão ativa.')); return false; }
      if (!consent) { report(issue('CONSENT_REQUIRED', 'A autorização precisa ser confirmada.')); return false; }
      if (!state.fileInfo) { report(issue('NO_APK', 'O APK ainda não foi validado.')); return false; }
      if (!sdk) { report(issue('SDK_LOAD', 'Prepare os componentes USB antes de conectar.')); return false; }
      state.busy = true; state.error = null; state.closeResult = null; state.cleanup = null; state.device = null; state.installVerified = false; state.manualChecked = false; state.progress = null; activeAbort = new Abort(); const abort = activeAbort;
      move('selecting-device', 'Escolha seu Android na janela USB. Depois, desbloqueie o celular e confira a solicitação de depuração RSA.');
      const warningTimer = timers.setTimeout(() => log('Ainda aguardando a conexão ou a confirmação no celular. Confira a tela do aparelho. Você pode cancelar esta etapa.', 'warning'), 30000);
      const deadlineTimer = timers.setTimeout(() => abort.abort(issue('CONNECT_TIMEOUT', 'A conexão excedeu 60 segundos. Confira o pedido RSA e as opções de desenvolvedor no celular.')), 60000);
      try {
        // Não há await antes de createClient: o seletor USB conserva o gesto do clique.
        const pending = sdk.createClient({ onStage: value => { if (activeAbort === abort && (!abort.signal.aborted || value && value.stage === 'warning')) sdkStage(value); }, signal: abort.signal });
        Promise.resolve(pending).then(late => { if (abort.signal.aborted && late && late !== client) Promise.resolve(late.close({ forget: true })).then(result => { log('Conexão recebida após o cancelamento: ' + (result && result.closed ? 'encerramento confirmado.' : 'encerramento não confirmado; confira o celular.'), 'warning'); if (result && Array.isArray(result.errors)) result.errors.forEach(error => log('Detalhe do encerramento tardio: ' + safeText(typeof error === 'string' ? error : (error.step || '') + ': ' + (error.message || 'Falha sem detalhes')), 'warning')); }, error => log('Falha no encerramento após cancelamento: ' + safeText(error && error.message) + '. Confira manualmente o celular.', 'warning')); }, () => {});
        client = await withAbort(pending, abort.signal);
        move('checking-device', 'Conexão autorizada. Conferindo marca, modelo, Android e arquiteturas compatíveis.');
        const model = await withAbort(client.model(), abort.signal);
        state.device = { manufacturer: safeText(model.manufacturer, 100), model: safeText(model.model, 100), apiLevel: Number(model.apiLevel), abis: Array.isArray(model.abis) ? model.abis.map(value => safeText(value, 40)) : [] };
        const compatible = model.compatible === true && state.device.apiLevel >= 26 && state.device.abis.some(abi => abi === 'arm64-v8a' || abi === 'x86_64');
        if (!compatible) throw issue('INCOMPATIBLE', 'Modelo consultado não confirmou Android/API e arquitetura compatíveis.');
        state.connected = true; move('connected', 'Aparelho compatível confirmado. A instalação só começa quando você clicar em “Instalar AliançaDX”.'); return true;
      } catch (error) { report(abort.signal.aborted ? (abort.signal.reason && abort.signal.reason.code ? abort.signal.reason : issue('CANCELLED', 'A conexão foi cancelada. Confira as opções de desenvolvedor no celular.')) : error); await closeClient(); return false; }
      finally { timers.clearTimeout(warningTimer); timers.clearTimeout(deadlineTimer); state.busy = false; publish(); }
    }
    async function cleanup() {
      if (!client || !state.installVerified) { log('O desligamento pelo assistente não pode ser solicitado sem uma instalação confirmada. Faça a conferência manual no celular.', 'warning'); return null; }
      move('cleanup', 'Solicitando ao Android o desligamento das opções de desenvolvedor e da depuração USB. A conexão pode cair antes da confirmação.');
      try {
        state.cleanup = await client.cleanupDeveloperOptions();
        if (state.cleanup && state.cleanup.developerOptions === 'confirmed-off') log('O Android confirmou o interruptor de opções de desenvolvedor desligado na leitura disponível. Confira também no próprio celular.');
        else log('O desligamento das opções de desenvolvedor não foi confirmado. Confira e desligue manualmente no celular.', 'warning');
        log(state.cleanup && state.cleanup.usbDebugging === 'confirmed-off' ? 'A leitura disponível indicou depuração USB desligada. Faça a conferência manual.' : 'O estado final da depuração USB precisa ser conferido manualmente. Perder a conexão não comprova que foi desligada.', 'warning');
        if (state.cleanup && Array.isArray(state.cleanup.errors)) state.cleanup.errors.forEach(error => log('Detalhe do desligamento: ' + safeText(typeof error === 'string' ? error : (error.step || '') + ': ' + (error.message || 'Falha sem detalhes')), 'warning'));
        else if (state.cleanup && state.cleanup.error) log('Detalhe do desligamento: ' + safeText(state.cleanup.error), 'warning');
        return state.cleanup;
      } catch (error) { state.cleanup = { developerOptions: 'unconfirmed', usbDebugging: 'unconfirmed', disconnected: true, manualVerificationRequired: true }; log('Não foi possível confirmar o desligamento pelo USB: ' + safeText(error && error.message) + ' Confira e desligue as opções no celular.', 'warning'); return state.cleanup; }
    }
    async function install(options) {
      if (state.busy || !client || !state.connected) { report(issue('BUSY', 'Conecte um aparelho compatível e aguarde antes de instalar.')); return false; }
      state.busy = true; state.error = null; state.progress = null; state.manualChecked = false;
      const abort = activeAbort || new Abort(); activeAbort = abort;
      let verified = false, lastLogged = -1;
      move('uploading', 'Enviando os bytes do APK validado diretamente ao celular pelo cabo USB.');
      const waitTimer = timers.setTimeout(() => log('A instalação ainda está em andamento. Confira a tela do Android e o progresso real de transferência. Você pode cancelar; o resultado no aparelho precisará ser verificado.', 'warning'), 45000);
      try {
        const result = await withAbort(client.install(state.fileInfo.file, progress => {
          if (activeAbort !== abort || abort.signal.aborted || !progress || progress.phase !== 'upload') return;
          const sent = Number(progress.sent), total = Number(progress.total);
          if (!Number.isFinite(sent) || !Number.isFinite(total) || total <= 0 || sent < 0 || sent > total) return;
          state.progress = { sent, total, phase: 'upload' };
          const percent = Math.floor(sent / total * 100);
          if (sent === total || Math.floor(percent / 25) > lastLogged) { lastLogged = Math.floor(percent / 25); log('Transferência do APK: ' + sent.toLocaleString('pt-BR') + ' de ' + total.toLocaleString('pt-BR') + ' bytes.'); }
          else publish();
          if (sent === total) { state.message = 'Todos os bytes foram encaminhados à transferência. Aguardando a confirmação USB; 100% de envio ainda não confirma a instalação.'; publish(); }
        }), abort.signal);
        if (!result || result.installed !== true || result.verified !== true || result.packageName !== APK.packageName || Number(result.versionCode) !== APK.versionCode) throw issue('VERIFICATION_FAILED', 'O resultado não confirmou pacote e versão oficiais esperados.');
        state.installVerified = true; verified = true; state.progress = null; move('installed', 'Instalação confirmada: AliançaDX 6.2.0, pacote e versão conferidos pelo Android.');
        if (options && options.cleanup === true) await cleanup();
        else log('O desligamento automático não foi selecionado. Desative a depuração USB e as opções de desenvolvedor no celular.', 'warning');
        return true;
      } catch (error) { state.installVerified = false; report(abort.signal.aborted ? issue('CANCELLED', 'Cancelamento solicitado durante a instalação. O aplicativo pode ter sido instalado antes da interrupção; confira no celular.') : error); return false; }
      finally {
        timers.clearTimeout(waitTimer); await closeClient(); state.busy = false; state.progress = null;
        if (verified) move('completed', 'Aplicativo instalado. Falta você conferir no celular o desligamento da depuração USB e das opções de desenvolvedor.');
        else log('Antes de encerrar, confira manualmente o aplicativo e desligue a depuração USB e as opções de desenvolvedor.', 'warning');
        publish();
      }
    }
    async function cancel() {
      if (activeAbort && !activeAbort.signal.aborted) activeAbort.abort();
      log('Cancelamento solicitado. Encerrando a conexão; o estado final deve ser conferido no próprio celular.', 'warning');
      await closeClient();
      if (!state.busy) { state.stage = 'cancelled'; state.message = 'Conexão encerrada ou cancelamento solicitado. Confira as opções de desenvolvedor no celular.'; publish(); }
    }
    async function disconnect() { if (state.busy) return cancel(); state.busy = true; publish(); try { await closeClient(); move('closed', 'Pedido de encerramento concluído. Confira o resultado no histórico e desative a depuração USB e as opções de desenvolvedor no celular.'); } finally { state.busy = false; publish(); } }
    function confirmManual(value) { if (!state.installVerified) return false; state.manualChecked = Boolean(value); log(state.manualChecked ? 'Você declarou ter conferido no celular o desligamento. Esta confirmação é manual; não é uma leitura USB.' : 'A conferência manual continua pendente.', state.manualChecked ? 'info' : 'warning'); }
    return { getState: snapshot, prepare, selectFile, connect, install, cleanup, cancel, disconnect, confirmManual };
  }
  function init(document, env, options) {
    bindHashControls(document, env);
    const root = document.getElementById('usb-instalacao'); if (!root || root.dataset.aadxReady === 'true') return;
    root.dataset.aadxReady = 'true'; options = options || {};
    const $ = selector => root.querySelector(selector);
    const file = $('#aadx-usb-apk'), consent = $('#aadx-usb-consent'), cleanupChoice = $('#aadx-usb-cleanup-choice');
    const connect = $('#aadx-usb-connect'), install = $('#aadx-usb-install'), cancel = $('#aadx-usb-cancel'), disconnect = $('#aadx-usb-disconnect'), reload = $('#aadx-usb-reload');
    const status = $('#aadx-usb-stage'), next = $('#aadx-usb-next'), fileStatus = $('#aadx-usb-file-status'), deviceInfo = $('#aadx-usb-device'), deviceText = $('#aadx-usb-device-text');
    const progressBox = $('#aadx-usb-progress-box'), progress = $('#aadx-usb-progress'), progressText = $('#aadx-usb-progress-text'), errorBox = $('#aadx-usb-error'), errorTitle = $('#aadx-usb-error-title'), errorAction = $('#aadx-usb-error-action'), errorDetail = $('#aadx-usb-error-detail');
    const logs = $('#aadx-usb-log'), finished = $('#aadx-usb-finished'), manual = $('#aadx-usb-manual-check');
    const nav = env.navigator || {};
    const supported = Boolean(env.isSecureContext && nav.usb && env.crypto && env.crypto.subtle);
    const loadSdk = options.loadSdk || (() => import('./vendor/aliancadx-adb.bundle.js?v=usb2'));
    function update(state) {
      status.textContent = state.message;
      next.textContent = state.busy ? 'Acompanhe a etapa ativa e confira a tela do celular. Use Cancelar se precisar interromper.' : state.installVerified ? (state.manualChecked ? 'Conferência manual registrada. Você pode desconectar o cabo.' : 'Abra as Configurações do celular e faça a conferência final abaixo.') : state.connected ? 'Clique em Instalar AliançaDX somente quando estiver pronto.' : state.fileInfo ? 'Conclua os passos manuais no celular, confirme a autorização e conecte o aparelho.' : 'Baixe o APK pelo link e selecione o arquivo local para validar.';
      file.disabled = state.busy || state.connected || state.validating;
      consent.disabled = state.busy || state.connected;
      cleanupChoice.disabled = state.busy;
      connect.disabled = !supported || !state.sdkReady || !state.fileInfo || !consent.checked || state.busy || state.validating || state.connected;
      install.disabled = !state.connected || state.busy || !state.fileInfo;
      cancel.disabled = !state.busy;
      disconnect.disabled = !state.connected || state.busy;
      reload.disabled = !supported || state.busy || state.sdkReady;
      root.setAttribute('aria-busy', String(state.busy || state.validating));
      fileStatus.textContent = state.fileInfo ? 'APK 6.2.0 conferido: tamanho e SHA-256 correspondem. Os mesmos bytes validados serão enviados ao celular.' : state.validating ? 'Conferindo o arquivo local…' : state.error && /APK|CRYPTO/.test(state.error.code) ? state.error.title : 'Nenhum APK validado nesta sessão.';
      deviceInfo.hidden = !state.device;
      if (state.device) { deviceInfo.hidden = false; deviceText.textContent = (state.device.manufacturer || 'Fabricante não informado') + ' · ' + (state.device.model || 'Modelo não informado') + ' · Android API ' + state.device.apiLevel + ' · ' + state.device.abis.join(', '); }
      progressBox.hidden = !state.busy;
      if (state.progress && state.stage === 'uploading') { progress.max = state.progress.total; progress.value = state.progress.sent; progressText.textContent = state.progress.sent.toLocaleString('pt-BR') + ' / ' + state.progress.total.toLocaleString('pt-BR') + ' bytes enviados (' + Math.floor(state.progress.sent / state.progress.total * 100) + '% da transferência).'; }
      else { progress.removeAttribute('value'); progress.max = 1; progressText.textContent = state.stage === 'installing' ? 'Aguardando o instalador e a conferência do Android. Sem estimativa de porcentagem.' : 'Aguardando a etapa ativa. Sem estimativa de porcentagem.'; }
      errorBox.hidden = !state.error;
      if (state.error) { errorTitle.textContent = state.error.title; errorAction.textContent = state.error.action; errorDetail.textContent = 'Código: ' + state.error.code + '\n' + state.error.detail; }
      finished.hidden = !state.installVerified;
      manual.checked = state.manualChecked;
      root.querySelectorAll('[data-aadx-usb-stage]').forEach(item => {
        const id = item.dataset.aadxUsbStage;
        const active = id === 'file' ? state.stage === 'validating-apk' : id === 'connect' ? ['selecting-device', 'authorizing', 'checking-device'].includes(state.stage) : id === 'install' ? ['connected', 'uploading', 'installing'].includes(state.stage) : id === 'cleanup' ? ['cleanup', 'installed', 'completed'].includes(state.stage) : false;
        if (active) item.setAttribute('aria-current', 'step'); else item.removeAttribute('aria-current');
      });
    }
    const controller = createController({ crypto: env.crypto, AbortController: env.AbortController, loadSdk, onState: update, onEvent: entry => {
      const row = document.createElement('li'), time = document.createElement('time'), text = document.createElement('span');
      time.dateTime = entry.time; time.textContent = new Date(entry.time).toLocaleTimeString('pt-BR'); text.textContent = entry.message; row.dataset.level = entry.level; row.appendChild(time); row.appendChild(text); logs.appendChild(row); if (logs.children.length > 100) logs.firstElementChild.remove();
    } });
    root.querySelectorAll('[data-aadx-usb-js]').forEach(element => { element.hidden = false; });
    file.addEventListener('change', () => controller.selectFile(file.files && file.files[0]));
    consent.addEventListener('change', () => update(controller.getState()));
    function showProgress() { const console = $('#aadx-usb-console'); if (console && typeof console.focus === 'function') console.focus({ preventScroll: true }); if (console && typeof console.scrollIntoView === 'function') console.scrollIntoView({ block: 'start', behavior: 'auto' }); }
    connect.addEventListener('click', () => { controller.connect(consent.checked); showProgress(); });
    install.addEventListener('click', () => { controller.install({ cleanup: cleanupChoice.checked }); showProgress(); });
    cancel.addEventListener('click', () => controller.cancel());
    disconnect.addEventListener('click', () => controller.disconnect());
    reload.addEventListener('click', () => controller.prepare());
    manual.addEventListener('change', () => controller.confirmManual(manual.checked));
    const diagnosticButton = $('#aadx-usb-copy-diagnostic');
    if (diagnosticButton) {
      diagnosticButton.hidden = false;
      diagnosticButton.addEventListener('click', () => copyDiagnostic(controller.getState(), { button: diagnosticButton, status: $('#aadx-usb-diagnostic-status'), fallback: $('#aadx-usb-diagnostic-fallback') }, env));
    }
    root.addEventListener('toggle', () => { if (root.open && supported) controller.prepare(); });
    if (!supported) $('#aadx-usb-environment').textContent = 'Este ambiente não oferece os requisitos do assistente USB. Abra a página HTTPS em Chrome ou Edge atualizado no computador. O tutorial manual continua disponível.';
    else $('#aadx-usb-environment').textContent = 'Os componentes USB serão preparados ao abrir esta seção. O navegador só pedirá o dispositivo quando você clicar em Conectar Android.';
    update(controller.getState()); if (root.open && supported) controller.prepare();
    return controller;
  }
  return { APK, validateApk, formatError, safeText, copyHash, bindHashControls, buildDiagnostic, copyDiagnostic, createController, init };
});
