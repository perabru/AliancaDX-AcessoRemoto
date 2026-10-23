/** Test-only JSON-pipe adapter around the real mobile transfer runtime. */
import { createInterface } from 'node:readline';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash, webcrypto } from 'node:crypto';
import { MobileFiles } from '../../files.mjs';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
let exchange = null, settings = {}, sentChunks = 0, receivedChunks = 0;

async function command(value) {
  switch (value.action) {
    case 'init':
      settings = value;
      exchange = new MobileFiles(message => {
        emit({ event: 'wire', message });
        const cancel = message.type === 'file_chunk' && ++sentChunks === settings.cancelSentChunks ||
          message.type === 'file_ack' && ++receivedChunks === settings.cancelReceivedChunks;
        if (cancel) queueMicrotask(() => exchange.cancel('Cancelado pela fixture de interoperabilidade.'));
        return true;
      }, (message, progress, busy) => emit({ event: 'status', message, progress, busy }),
      async () => settings.accept !== false,
      (blob, name) => {
        blob.arrayBuffer().then(async buffer => {
          const bytes = new Uint8Array(buffer);
          await writeFile(settings.destination, bytes);
          emit({ event: 'download', name, size: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') });
        }).catch(error => emit({ event: 'error', message: error.message }));
      });
      emit({ event: 'ready' });
      break;
    case 'wire': exchange.handle(value.message); break;
    case 'upload': {
      const bytes = new Uint8Array(await readFile(value.path));
      await exchange.upload({ name: value.name, size: bytes.length,
        arrayBuffer: async () => bytes.slice().buffer,
        slice: (start, end) => new Blob([bytes.slice(start, end)]) });
      break;
    }
    case 'request': exchange.request(); break;
    case 'cancel': exchange.cancel(); break;
    case 'close': exchange.close(); process.exit(0); break;
    default: throw new Error('Unknown fixture command.');
  }
}

createInterface({ input: process.stdin, crlfDelay: Infinity }).on('line', line => {
  Promise.resolve().then(() => command(JSON.parse(line)))
    .catch(error => emit({ event: 'error', message: error.message }));
}).on('close', () => { exchange?.close(); process.exit(0); });
