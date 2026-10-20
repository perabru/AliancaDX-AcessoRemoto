import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {dirname,resolve} from 'node:path';
const here=dirname(fileURLToPath(import.meta.url));
await build({entryPoints:[resolve(here,'../../src/usb-adb-transport.js')],outfile:resolve(here,'../../assets/vendor/aliancadx-adb.bundle.js'),bundle:true,format:'esm',platform:'browser',target:['chrome102','edge102'],minify:true,legalComments:'eof',nodePaths:[resolve(here,'node_modules')]});
