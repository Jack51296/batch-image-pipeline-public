const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { test, before, after } = require('node:test');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
let browser, temporary, fixtures;

before(async () => {
  temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'pipeline-image-tools-'));
  browser = await chromium.launch({
    headless: true,
    ...(process.env.IMAGE_TOOLS_BROWSER_CHANNEL ? { channel: process.env.IMAGE_TOOLS_BROWSER_CHANNEL } : {})
  });
  const page = await browser.newPage();
  fixtures = {};
  for (const [name, width, height, type, noisy] of [
    ['source', 160, 120, 'image/png', true],
    ['large', 4000, 2000, 'image/png', false],
    ['tiny', 2, 2, 'image/png', false],
    ['targetPng', 96, 80, 'image/png', false],
    ['targetJpeg', 96, 80, 'image/jpeg', true]
  ]) {
    const bytes = await page.evaluate(async ({ width, height, type, noisy }) => {
      const canvas = document.createElement('canvas');
      canvas.width = width; canvas.height = height;
      const ctx = canvas.getContext('2d');
      if (noisy) {
        const pixels = ctx.createImageData(width, height);
        for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
          const offset = (y * width + x) * 4;
          pixels.data.set([(x * 17 + y * 31) % 256, (x * 37 + y * 13) % 256, (x * 7 + y * 23) % 256, 255], offset);
        }
        ctx.putImageData(pixels, 0, 0);
      } else {
        ctx.fillStyle = '#397ac6'; ctx.fillRect(0, 0, width, height);
      }
      const blob = await new Promise(resolve => canvas.toBlob(resolve, type, .8));
      return [...new Uint8Array(await blob.arrayBuffer())];
    }, { width, height, type, noisy });
    const destination = path.join(temporary, `${name}.${type === 'image/jpeg' ? 'jpg' : 'png'}`);
    await fs.writeFile(destination, Buffer.from(bytes));
    fixtures[name] = destination;
  }
  await fs.writeFile(path.join(temporary, 'broken.png'), 'not an image');
  await page.close();
});

after(async () => {
  await browser?.close();
  if (temporary) await fs.rm(temporary, { recursive: true, force: true });
});

async function open(relative) {
  const filename = path.join(root, relative);
  await fs.access(filename);
  const page = await browser.newPage({ acceptDownloads: true });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const network = [];
  page.on('request', request => { if (/^https?:/.test(request.url())) network.push(request.url()); });
  await page.goto(pathToFileURL(filename).href);
  page.testErrors = errors;
  page.testNetwork = network;
  return page;
}

async function download(page, selector) {
  const waiting = page.waitForEvent('download');
  await page.locator(selector).click();
  const result = await waiting;
  const destination = path.join(temporary, result.suggestedFilename());
  await result.saveAs(destination);
  const bytes = await fs.readFile(destination);
  const decoder = await browser.newPage();
  await decoder.goto(pathToFileURL(destination).href);
  const dimensions = await decoder.locator('img').evaluate(img => ({ width: img.naturalWidth, height: img.naturalHeight }));
  await decoder.close();
  assert.ok(dimensions.width > 0 && dimensions.height > 0, 'downloaded image must decode');
  assert.deepEqual(page.testErrors, [], 'no uncaught browser errors');
  assert.deepEqual(page.testNetwork, [], 'image tools must work without network requests');
  return { destination, bytes, filename: result.suggestedFilename(), ...dimensions };
}

test('portal and tool navigation work as local files', async () => {
  const page = await open('index.html');
  for (const [label, suffix] of [['比例裁剪', 'ratio_crop/index.html'], ['尺寸与文件大小匹配', 'size_matcher/index.html']]) {
    const link = page.getByRole('link', { name: label, exact: true });
    await link.click();
    assert.ok(page.url().endsWith(suffix));
    await page.getByRole('link', { name: '工具首页', exact: true }).click();
    assert.equal(page.url(), pathToFileURL(path.join(root, 'index.html')).href);
  }
  await page.close();
});

test('ratio crop preserves pixels, supports 13 ratios and drag positioning', async () => {
  const page = await open('ratio_crop/index.html');
  await page.locator('#fileInput').setInputFiles(fixtures.source);
  await page.waitForFunction(() => document.getElementById('cropSize').textContent === '160 × 120');
  assert.equal(await page.locator('.ratio').count(), 13);
  await page.getByRole('button', { name: '1:1', exact: true }).click();
  const result = await download(page, '#downloadButton');
  assert.deepEqual([result.width, result.height], [120, 120]);
  assert.match(result.filename, /1x1_120x120\.png$/);
  const decoded = await browser.newPage();
  await decoded.goto(pathToFileURL(result.destination).href);
  const pixels = await decoded.locator('img').evaluate(img => {
    const canvas = document.createElement('canvas'); canvas.width = 120; canvas.height = 120;
    const ctx = canvas.getContext('2d'); ctx.drawImage(img, 0, 0);
    return [...ctx.getImageData(0, 0, 120, 120).data];
  });
  for (let y = 0; y < 120; y++) for (let x = 0; x < 120; x++) {
    const sourceX = x + 20, offset = (y * 120 + x) * 4;
    assert.deepEqual(pixels.slice(offset, offset + 4), [(sourceX * 17 + y * 31) % 256, (sourceX * 37 + y * 13) % 256, (sourceX * 7 + y * 23) % 256, 255]);
  }
  await decoded.close();
  const bounds = await page.locator('#previewCanvas').boundingBox();
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down(); await page.mouse.move(bounds.x + bounds.width / 2 - 45, bounds.y + bounds.height / 2); await page.mouse.up();
  const dragged = await download(page, '#downloadButton');
  assert.notDeepEqual(dragged.bytes, result.bytes, 'dragging must change the selected region');
  await page.close();
});

test('large images shrink to a 3000 pixel longest edge and all crop formats decode', async () => {
  const page = await open('ratio_crop/index.html');
  await page.locator('#fileInput').setInputFiles(fixtures.large);
  await page.waitForFunction(() => document.getElementById('sourceSize').textContent === '3000 × 1500');
  assert.match(await page.locator('#scaleNote').textContent(), /4000 × 2000/);
  for (const [format, extension] of [['image/png', 'png'], ['image/jpeg', 'jpg'], ['image/webp', 'webp']]) {
    await page.locator('#formatSelect').selectOption(format);
    const result = await download(page, '#downloadButton');
    assert.deepEqual([result.width, result.height], [3000, 1500]);
    assert.ok(result.filename.endsWith(`.${extension}`));
  }
  await page.close();
});

test('every crop ratio uses the largest possible exact integer dimensions', async () => {
  const page = await open('ratio_crop/index.html');
  await page.locator('#fileInput').setInputFiles(fixtures.source);
  await page.waitForFunction(() => document.getElementById('cropSize').textContent === '160 × 120');
  const ratios = [[1,1],[1,2],[2,1],[9,16],[16,9],[3,4],[4,3],[3,2],[2,3],[5,4],[4,5],[21,9],[9,21]];
  const gcd = (a, b) => b ? gcd(b, a % b) : a;
  for (const [w, h] of ratios) {
    await page.getByRole('button', { name: `${w}:${h}`, exact: true }).click();
    const unitW = w / gcd(w, h), unitH = h / gcd(w, h);
    const multiple = Math.min(Math.floor(160 / unitW), Math.floor(120 / unitH));
    assert.equal(await page.locator('#cropSize').textContent(), `${unitW * multiple} × ${unitH * multiple}`, `largest crop for ${w}:${h}`);
  }
  await page.close();
});

test('crop rejects unreadable files and disables impossible integer ratios', async () => {
  const page = await open('ratio_crop/index.html');
  assert.equal(await page.locator('#downloadButton').isDisabled(), true);
  await page.locator('#fileInput').setInputFiles(path.join(temporary, 'broken.png'));
  await page.waitForFunction(() => document.getElementById('errorText').textContent.includes('无法读取'));
  await page.locator('#fileInput').setInputFiles(fixtures.tiny);
  await page.waitForFunction(() => document.getElementById('cropSize').textContent === '2 × 2');
  await page.getByRole('button', { name: '21:9', exact: true }).click();
  assert.equal(await page.locator('#downloadButton').isDisabled(), true);
  assert.equal(await page.locator('#cropSize').textContent(), '尺寸不足');
  await page.close();
});

test('size matcher forces target dimensions and exact bytes in JPEG, WebP and PNG', async () => {
  const page = await open('size_matcher/index.html');
  await page.locator('#sourceInput').setInputFiles(fixtures.source);
  await page.locator('#targetInput').setInputFiles(fixtures.targetJpeg);
  await page.waitForFunction(() => !document.getElementById('process').disabled);
  for (const [format, extension] of [['image/jpeg', 'jpg'], ['image/webp', 'webp'], ['image/png', 'png']]) {
    await page.locator('#targetSize').fill('50000');
    await page.locator('#format').selectOption(format);
    const result = await download(page, '#process');
    assert.deepEqual([result.width, result.height], [96, 80]);
    assert.equal(result.bytes.length, 50000);
    assert.ok(result.filename.endsWith(`.${extension}`));
    assert.equal(await page.locator('#outDiff').textContent(), '0 B');
  }
  await page.locator('#reset').click();
  assert.equal(await page.locator('#process').isDisabled(), true);
  assert.equal(await page.locator('#sourcePreview').getAttribute('src'), null);
  await page.close();
});

test('size matcher auto format falls back from PNG to WebP and reports unattainable bytes', async () => {
  const page = await open('size_matcher/index.html');
  await page.locator('#sourceInput').setInputFiles(fixtures.source);
  await page.locator('#targetInput').setInputFiles(fixtures.targetPng);
  await page.waitForFunction(() => !document.getElementById('process').disabled);
  await page.locator('#targetSize').fill('4000');
  const result = await download(page, '#process');
  assert.deepEqual([result.width, result.height], [96, 80]);
  assert.ok(result.filename.endsWith('.webp'));
  assert.equal(result.bytes.length, 4000);
  await page.locator('#targetSize').fill('1');
  const tooSmall = await download(page, '#process');
  assert.ok(tooSmall.bytes.length > 1);
  assert.match(await page.locator('#status').textContent(), /误差/);
  assert.equal(await page.locator('#status').getAttribute('class'), 'status warn');
  await page.close();
});

test('size matcher validates files and finite positive integer target sizes', async () => {
  const page = await open('size_matcher/index.html');
  await page.locator('#sourceInput').setInputFiles(path.join(temporary, 'broken.png'));
  await page.waitForFunction(() => document.getElementById('status').textContent.includes('无法读取'));
  await page.locator('#sourceInput').setInputFiles(fixtures.source);
  await page.locator('#targetInput').setInputFiles(fixtures.targetPng);
  await page.waitForFunction(() => !document.getElementById('process').disabled);
  for (const invalid of ['0', '-1', '', '0.5', '1e309']) {
    await page.locator('#targetSize').fill(invalid);
    assert.equal(await page.locator('#process').isDisabled(), true, `invalid target ${invalid}`);
  }
  await page.close();
});
