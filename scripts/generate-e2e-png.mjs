// Synthetic PNG fixtures; stdout only, no filesystem or network IO.
import { deflateSync } from "node:zlib";
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const content = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(content));
  return Buffer.concat([length, content, crc]);
}
const variant = process.argv[2];
if (process.argv.length > 3 || (variant !== undefined && !["preview-front", "preview-side"].includes(variant))) throw new Error("Unknown synthetic fixture");
const width = variant ? 768 : 1024, height = variant ? 432 : 1024;
const raw = Buffer.alloc(height * (width * 3 + 1));
if (variant) {
  for (let y = 0; y < height; y += 1) for (let x = 0; x < width; x += 1) {
    const offset = y * (width * 3 + 1) + 1 + x * 3;
    const shade = ((Math.floor(x / 64) + Math.floor(y / 54)) % 2) * 25;
    raw[offset] = (variant === "preview-front" ? 130 : 75) + shade;
    raw[offset + 1] = 130 + shade; raw[offset + 2] = (variant === "preview-front" ? 100 : 150) + shade;
  }
}
const header = Buffer.alloc(13);
header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2;
const png = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", header),
  chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
process.stdout.write(png.toString("base64"));
