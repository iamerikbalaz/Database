// Fixed synthetic 1024x1024 RGB PNG; stdout only, no filesystem or network IO.
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
const header = Buffer.alloc(13);
header.writeUInt32BE(1024, 0); header.writeUInt32BE(1024, 4); header[8] = 8; header[9] = 2;
const png = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", header),
  chunk("IDAT", deflateSync(Buffer.alloc(1024 * (1024 * 3 + 1)))), chunk("IEND", Buffer.alloc(0))]);
process.stdout.write(png.toString("base64"));
