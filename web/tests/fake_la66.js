// A simulated Dragino LA66 (P2P firmware v1.2.4) behind a fake Web Serial API.
// Injected into the page by e2e_browser.py before the app loads.
//   window.__la66.sent      frames the app transmitted (hex)
//   window.__la66.cmds      every AT command written
//   window.__la66.rx(hex, rssi, snr)   make the radio "hear" a frame
//   window.__la66.unplug()  simulate pulling the stick out
(() => {
  const st = { fre: "920.800", sf: 12, bw: 0, cr: 1, pre: 8, pow: 14, sync: 0, crc: 1 };   // factory: SF12
  const enc = new TextEncoder();
  let ctrl = null, last = null, plugged = true;
  const lines = (...ls) => { for (const l of ls) setTimeout(() => ctrl && ctrl.enqueue(enc.encode(l + "\r\n")), 0); };
  const spaced = (hex) => hex.match(/../g).join(" ") + " ";
  const bwKhz = ["125", "250", "500", "62.5"][st.bw] || "125";
  const api = {
    sent: [], cmds: [],
    rx(hex, rssi = -87, snr = 9) {
      last = { hex: hex.toUpperCase(), rssi, snr };
      lines("rxDone", "Data: (HEX:) " + spaced(hex.toUpperCase()), `Rssi= ${rssi}`);
    },
    unplug() { plugged = false; try { ctrl.error(new Error("device lost")); } catch (e) {} fire("disconnect"); },
    plug(event = true) { plugged = true; if (event) fire("connect"); },
  };
  function handle(cmd) {
    api.cmds.push(cmd);
    let m;
    if (cmd === "ATZ") {
      const b = ["125", "250", "500", "62.5"][st.bw] || "125";
      lines("LA66 P2P Firmware v1.2.4", "Group: 0 , 0", `Syncword: ${st.sync ? "0x3444" : "0x1424"}`,
        `Frequency: ${st.fre} , ${st.fre}`, `BandWidth: ${b} kHz , ${b} kHz`, `Spreading Factor: ${st.sf} , ${st.sf}`,
        `Output Power: ${st.pow} dBm`, `Coding Rate: 4/${st.cr + 4} , 4/${st.cr + 4}`,
        "Header Type: explicit header , explicit header", `CRC: ${st.crc ? "ON , ON" : "OFF , OFF"}`, "Invert IQ: 0 , 0",
        `Preamble: ${st.pre} , ${st.pre}`, "RX Mode: 65535 , 0");
    } else if ((m = /^AT\+SEND=0,([0-9A-F]+),0,0$/.exec(cmd))) {
      api.sent.push(m[1]);
      setTimeout(() => lines("txDone", `RX on freq ${Math.round(parseFloat(st.fre) * 1e6)}`), 80);
    } else if (cmd === "AT+RECV=0") {
      if (last) lines(`${spaced(last.hex)},${last.rssi},${last.snr}`);
    } else if ((m = /^AT\+FRE=([\d.]+),/.exec(cmd))) st.fre = Number(m[1]).toFixed(3);
    else if ((m = /^AT\+SF=(\d+),/.exec(cmd))) st.sf = Number(m[1]);
    else if ((m = /^AT\+BW=(\d+),/.exec(cmd))) st.bw = Number(m[1]);
    else if ((m = /^AT\+CR=(\d+),/.exec(cmd))) st.cr = Number(m[1]);
    else if ((m = /^AT\+PREAMBLE=(\d+),/.exec(cmd))) st.pre = Number(m[1]);
    else if ((m = /^AT\+POWER=(\d+)/.exec(cmd))) st.pow = Number(m[1]);
    else if ((m = /^AT\+SYNCWORD=(\d)/.exec(cmd))) st.sync = Number(m[1]);
    else if ((m = /^AT\+CRC=(\d),/.exec(cmd))) st.crc = Number(m[1]);
  }
  let buf = "";
  const port = {};
  Object.assign(port, {
    readable: null, writable: null,
    async open() {
      if (!plugged) throw new Error("device not found");
      this.readable = new ReadableStream({ start(c) { ctrl = c; } });
      this.writable = new WritableStream({ write(chunk) {
        buf += new TextDecoder().decode(chunk);
        let i;
        while ((i = buf.indexOf("\r\n")) >= 0) { const c = buf.slice(0, i); buf = buf.slice(i + 2); handle(c); }
      } });
    },
    async close() { this.readable = null; this.writable = null; },
    getInfo() { return { usbVendorId: 0x10c4, usbProductId: 0xea60 }; },
  });
  // real Web Serial fires connect/disconnect at the port and they bubble to navigator.serial (target = port)
  const listeners = { connect: [], disconnect: [] };
  const fire = (type) => { for (const f of listeners[type]) f({ type, target: port }); };
  let granted = sessionStorage.getItem("fakeGranted") === "1";      // the browser remembers approvals
  const serial = {
    addEventListener(type, f) { (listeners[type] || (listeners[type] = [])).push(f); },
    removeEventListener() {},
    async requestPort() { if (api.denyPicker) throw new DOMException("No port selected", "NotFoundError"); plugged = true; granted = true; sessionStorage.setItem("fakeGranted", "1"); api.picker++; return port; },
    async getPorts() { return granted && plugged ? [port] : []; },
  };
  api.picker = 0;
  Object.defineProperty(navigator, "serial", { value: serial, configurable: true });
  window.__la66 = api;
})();
