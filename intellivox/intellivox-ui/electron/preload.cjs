const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('intellivox', {
  platform: process.platform,
  isDesktop: true,
});
