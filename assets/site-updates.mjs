import { checkRelease } from '../ios-pwa/updates.mjs';

const installedVersion = '7.6.1';
const button = document.getElementById('site-update-check');
const status = document.getElementById('site-update-status');
const releaseLink = document.getElementById('site-update-release');

button?.addEventListener('click', async () => {
  if (button.disabled) return;
  button.disabled = true;
  button.textContent = 'Buscando atualização…';
  status.textContent = 'Consultando as versões publicadas no GitHub…';
  releaseLink.hidden = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    if (navigator.onLine === false) throw new Error('Sem internet. Reconecte e tente novamente.');
    const result = await checkRelease({ installedVersion, signal: controller.signal });
    if (result.newer) {
      status.textContent = `A versão ${result.version} está disponível. Esta página apresenta ${installedVersion}.`;
      releaseLink.href = result.releaseUrl;
      releaseLink.textContent = `Ver downloads da versão ${result.version}`;
      releaseLink.hidden = false;
    } else if (result.version === installedVersion) {
      status.textContent = `Esta página já apresenta a versão ${installedVersion}, a última publicada.`;
    } else {
      status.textContent = `Esta página apresenta ${installedVersion}. A última versão no GitHub é ${result.version}; aguarde a conclusão da publicação.`;
    }
  } catch (error) {
    status.textContent = controller.signal.aborted
      ? 'A consulta demorou demais. Confira a internet e tente novamente.'
      : String(error?.message || 'Não foi possível consultar o GitHub. Tente novamente.').slice(0, 400);
  } finally {
    clearTimeout(timeout);
    button.disabled = false;
    button.textContent = 'Buscar atualização';
  }
});
