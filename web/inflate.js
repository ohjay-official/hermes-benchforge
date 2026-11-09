/* Raw DEFLATE (RFC 1951) decoder with an optional preset dictionary, for live links.
 *
 * Browsers can inflate on their own (DecompressionStream), but not with a preset dictionary. Live
 * links are compressed against a dictionary in web/dicts/ (the golden benches' firmware), which makes them
 * short enough to send in a chat message, so the page decodes them here. Follows zlib's puff.c.
 */
(function (root) {
  'use strict';
  var LBASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258];
  var LEXT = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
  var DBASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073,
    4097, 6145, 8193, 12289, 16385, 24577];
  var DEXT = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13];
  var ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];
  var MAX_OUT = 1 << 22;                     // 4 MB: far more than any payload; stops a hostile link

  function table(lengths, start, num) {
    var counts = new Uint16Array(16), offs = new Uint16Array(16), syms = new Uint16Array(num), i;
    for (i = 0; i < num; i++) counts[lengths[start + i]]++;
    counts[0] = 0;
    for (i = 1; i < 16; i++) offs[i] = offs[i - 1] + counts[i - 1];
    for (i = 0; i < num; i++) if (lengths[start + i]) syms[offs[lengths[start + i]]++] = i;
    return { counts: counts, syms: syms };
  }

  var FIXED_LIT, FIXED_DIST;
  (function () {
    var l = new Uint8Array(288), i;
    for (i = 0; i < 144; i++) l[i] = 8;
    for (; i < 256; i++) l[i] = 9;
    for (; i < 280; i++) l[i] = 7;
    for (; i < 288; i++) l[i] = 8;
    FIXED_LIT = table(l, 0, 288);
    var d = new Uint8Array(30);
    for (i = 0; i < 30; i++) d[i] = 5;
    FIXED_DIST = table(d, 0, 30);
  })();

  function inflateRaw(src, dict) {
    var pos = 0, buf = 0, cnt = 0, n = 0;
    var out = new Uint8Array(Math.max(4096, (dict ? dict.length : 0) + src.length * 4));
    if (dict) { out.set(dict, 0); n = dict.length; }
    var start = n;

    function bits(k) {
      while (cnt < k) {
        if (pos >= src.length) throw new Error('This link is cut short.');
        buf |= src[pos++] << cnt;
        cnt += 8;
      }
      var v = buf & ((1 << k) - 1);
      buf >>>= k;
      cnt -= k;
      return v;
    }
    function room(k) {
      if (n + k <= out.length) return;
      if (n + k > MAX_OUT) throw new Error('This link unpacks to too much data.');
      var bigger = new Uint8Array(Math.min(MAX_OUT, Math.max(out.length * 2, n + k)));
      bigger.set(out.subarray(0, n));
      out = bigger;
    }
    function decode(h) {
      var code = 0, first = 0, index = 0;
      for (var len = 1; len < 16; len++) {
        code |= bits(1);
        var count = h.counts[len];
        if (code - count < first) return h.syms[index + (code - first)];
        index += count;
        first = (first + count) << 1;
        code <<= 1;
      }
      throw new Error('This link is damaged.');
    }
    function codes(lit, dist) {
      for (;;) {
        var sym = decode(lit);
        if (sym < 256) { room(1); out[n++] = sym; continue; }
        if (sym === 256) return;
        sym -= 257;
        if (sym >= 29) throw new Error('This link is damaged.');
        var len = LBASE[sym] + bits(LEXT[sym]);
        var ds = decode(dist);
        if (ds >= 30) throw new Error('This link is damaged.');
        var d = DBASE[ds] + bits(DEXT[ds]);
        if (d > n) throw new Error('This link is damaged.');
        room(len);
        for (var i = 0; i < len; i++, n++) out[n] = out[n - d];
      }
    }
    function dynamic() {
      var nlen = bits(5) + 257, ndist = bits(5) + 1, ncode = bits(4) + 4, i;
      if (nlen > 286 || ndist > 30) throw new Error('This link is damaged.');
      var lengths = new Uint8Array(320);
      for (i = 0; i < ncode; i++) lengths[ORDER[i]] = bits(3);
      var lencode = table(lengths, 0, 19);
      lengths = new Uint8Array(320);
      i = 0;
      while (i < nlen + ndist) {
        var sym = decode(lencode), rep, val = 0;
        if (sym < 16) { lengths[i++] = sym; continue; }
        if (sym === 16) {
          if (i === 0) throw new Error('This link is damaged.');
          val = lengths[i - 1];
          rep = 3 + bits(2);
        } else if (sym === 17) rep = 3 + bits(3);
        else rep = 11 + bits(7);
        if (i + rep > nlen + ndist) throw new Error('This link is damaged.');
        while (rep--) lengths[i++] = val;
      }
      codes(table(lengths, 0, nlen), table(lengths, nlen, ndist));
    }
    function stored() {
      buf = 0;
      cnt = 0;
      if (pos + 4 > src.length) throw new Error('This link is cut short.');
      var len = src[pos] | (src[pos + 1] << 8), nlen = src[pos + 2] | (src[pos + 3] << 8);
      pos += 4;
      if (len !== (~nlen & 0xffff)) throw new Error('This link is damaged.');
      if (pos + len > src.length) throw new Error('This link is cut short.');
      room(len);
      out.set(src.subarray(pos, pos + len), n);
      pos += len;
      n += len;
    }

    var last;
    do {
      last = bits(1);
      var type = bits(2);
      if (type === 0) stored();
      else if (type === 1) codes(FIXED_LIT, FIXED_DIST);
      else if (type === 2) dynamic();
      else throw new Error('This link is damaged.');
    } while (!last);
    return out.slice(start, n);
  }

  function adler32(b) {
    var a = 1, c = 0;
    for (var i = 0; i < b.length;) {
      var end = Math.min(b.length, i + 3800);
      for (; i < end; i++) { a += b[i]; c += a; }
      a %= 65521;
      c %= 65521;
    }
    return ((c << 16) | a) >>> 0;
  }

  // zlib format (RFC 1950): its header names the dictionary (DICTID) and its trailer checks the data,
  // so a link made with another dictionary, or damaged in transit, is refused instead of misread.
  function inflateZlib(src, dict) {
    if (src.length < 6 || (src[0] & 15) !== 8 || ((src[0] << 8) | src[1]) % 31) throw new Error('This link is damaged.');
    var body = 2;
    if (src[1] & 32) {
      var id = ((src[2] << 24) | (src[3] << 16) | (src[4] << 8) | src[5]) >>> 0;
      if (!dict || adler32(dict) !== id) throw new Error('This link was made by a different BenchForge version.');
      body = 6;
    } else dict = null;
    var out = inflateRaw(src.subarray(body, src.length - 4), dict);
    var t = src.length - 4;
    var check = ((src[t] << 24) | (src[t + 1] << 16) | (src[t + 2] << 8) | src[t + 3]) >>> 0;
    if (adler32(out) !== check) throw new Error('This link is damaged.');
    return out;
  }

  var api = { inflateRaw: inflateRaw, inflateZlib: inflateZlib, adler32: adler32 };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.BenchForgeInflate = api;
})(this);
