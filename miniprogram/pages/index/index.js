const FORMATS = {
  image: ['png', 'jpg', 'webp', 'bmp', 'tif', 'gif', 'ico', 'pdf'],
  audio: ['mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg'],
  video: ['mp4', 'mkv', 'mov', 'webm', 'avi', 'gif', 'mp3', 'wav', 'flac'],
  doc: ['pdf', 'docx', 'txt', 'md', 'html', 'png', 'jpg'],
  archive: ['zip', 'tar', 'tar.gz', 'tar.bz2']
};

const DEFAULTS = { image: 'png', audio: 'mp3', video: 'mp4', doc: 'pdf', archive: 'zip' };

const LABELS = { image: '图像', audio: '音频', video: '视频', doc: '文档', archive: '压缩包' };

const EXT_SETS = {
  image: ['png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif', 'tif', 'tiff', 'ico'],
  audio: ['mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg', 'wma', 'amr'],
  video: ['mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv', 'm4v', 'ts', 'mpg', 'mpeg'],
  doc: ['txt', 'md', 'markdown', 'html', 'htm', 'pdf', 'docx', 'doc', 'rtf'],
  archive: ['zip', 'tar', 'gz', 'tgz', 'bz2', 'tbz2', '7z', 'rar', 'xz', 'txz']
};

const IMAGE_EXTS = ['png', 'jpg', 'webp', 'bmp', 'tif', 'gif', 'ico'];
const VIDEO_EXTS = ['mp4', 'mkv', 'mov', 'webm', 'avi'];

function detect(name) {
  const lower = (name || '').toLowerCase();
  if (/(\.tar\.gz|\.tar\.bz2|\.tar\.xz)$/.test(lower)) {
    return { category: 'archive', format: 'tar' };
  }
  if (/\.(tgz|tbz2|tbz|txz)$/.test(lower)) {
    return { category: 'archive', format: 'tar' };
  }
  const match = lower.match(/\.([a-z0-9]+)$/);
  if (!match) return { category: null, format: null };
  let ext = match[1];
  const alias = { jpeg: 'jpg', tiff: 'tif', markdown: 'md', htm: 'html' };
  ext = alias[ext] || ext;
  for (const category of Object.keys(EXT_SETS)) {
    if (EXT_SETS[category].indexOf(ext) >= 0) {
      return { category, format: ext };
    }
  }
  return { category: null, format: null };
}

function pickTarget(category, sourceFormat) {
  const list = FORMATS[category];
  const preferred = DEFAULTS[category];
  if (preferred && preferred !== sourceFormat) return preferred;
  const other = list.find((item) => item !== sourceFormat);
  return other || preferred;
}

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function extOf(name) {
  const lower = (name || '').toLowerCase();
  if (/\.tar\.(gz|bz2|xz)$/.test(lower)) {
    return lower.split('.').slice(-2).join('.');
  }
  const index = lower.lastIndexOf('.');
  return index >= 0 ? lower.slice(index + 1) : '';
}

Page({
  data: {
    server: '',
    maxMb: 200,
    categoryLabels: LABELS,
    targets: FORMATS,
    files: [],
    results: [],
    busy: false,
    log: ''
  },

  onLoad() {
    const saved = wx.getStorageSync('serverUrl');
    this.setData({ server: saved || 'http://127.0.0.1:5000' });
  },

  onServerInput(event) {
    this.setData({ server: event.detail.value });
  },

  saveServer() {
    const server = (this.data.server || '').trim().replace(/\/+$/, '');
    if (!server) {
      wx.showToast({ title: '请填写服务器地址', icon: 'none' });
      return;
    }
    wx.setStorageSync('serverUrl', server);
    this.setData({ server });
    wx.showToast({ title: '已保存', icon: 'success' });
  },

  chooseFiles() {
    wx.chooseMessageFile({
      count: 9,
      type: 'file',
      success: (res) => {
        this.addFiles(res.tempFiles.map((file) => ({
          name: file.name,
          path: file.path,
          size: file.size
        })));
      }
    });
  },

  chooseMedia() {
    wx.chooseMedia({
      count: 9,
      mediaType: ['image', 'video'],
      success: (res) => {
        const files = res.tempFiles.map((file, index) => {
          const ext = extOf(file.tempFilePath) || (file.fileType === 'video' ? 'mp4' : 'jpg');
          const prefix = file.fileType === 'video' ? '视频' : '图片';
          return {
            name: `${prefix}_${index + 1}.${ext}`,
            path: file.tempFilePath,
            size: file.size
          };
        });
        this.addFiles(files);
      }
    });
  },

  addFiles(list) {
    if (this.data.busy) {
      wx.showToast({ title: '正在转换，请稍候', icon: 'none' });
      return;
    }
    const ignored = [];
    const files = [];
    list.forEach((file, index) => {
      const info = detect(file.name);
      if (!info.category) {
        ignored.push(file.name);
        return;
      }
      const target = pickTarget(info.category, info.format);
      files.push({
        id: `${Date.now()}_${index}_${Math.random().toString(16).slice(2, 6)}`,
        name: file.name,
        path: file.path,
        size: file.size,
        sizeText: formatSize(file.size),
        category: info.category,
        categoryText: LABELS[info.category],
        options: FORMATS[info.category],
        target,
        targetIndex: Math.max(0, FORMATS[info.category].indexOf(target)),
        status: '待转换',
        statusClass: ''
      });
    });

    if (files.length) {
      this.setData({
        files: this.data.files.concat(files),
        log: `已添加 ${files.length} 个文件`
      });
    }
    if (ignored.length) {
      wx.showToast({ title: `已忽略 ${ignored.length} 个不支持的文件`, icon: 'none' });
    }
  },

  onTargetChange(event) {
    const id = event.currentTarget.dataset.id;
    const index = Number(event.detail.value);
    const files = this.data.files.map((file) => {
      if (file.id !== id) return file;
      const target = FORMATS[file.category][index];
      return Object.assign({}, file, { target, targetIndex: index, status: '待转换', statusClass: '' });
    });
    this.setData({ files });
  },

  removeFile(event) {
    if (this.data.busy) return;
    const id = event.currentTarget.dataset.id;
    this.setData({ files: this.data.files.filter((file) => file.id !== id) });
  },

  clearAll() {
    if (this.data.busy) return;
    this.setData({ files: [], results: [], log: '已清空' });
  },

  setStatus(index, status, statusClass) {
    this.setData({
      [`files[${index}].status`]: status,
      [`files[${index}].statusClass`]: statusClass || ''
    });
  },

  baseUrl() {
    return (this.data.server || '').trim().replace(/\/+$/, '');
  },

  startConvert() {
    if (this.data.busy || !this.data.files.length) return;
    const base = this.baseUrl();
    if (!base) {
      wx.showToast({ title: '请先填写服务器地址', icon: 'none' });
      return;
    }
    this.setData({ busy: true, results: [], log: '开始转换…' });

    const run = async () => {
      let success = 0;
      let failed = 0;
      for (let index = 0; index < this.data.files.length; index += 1) {
        const ok = await this.convertOne(index, base);
        if (ok) success += 1; else failed += 1;
      }
      this.setData({
        busy: false,
        log: `全部结束：成功 ${success} 个，失败 ${failed} 个`
      });
      wx.showToast({ title: `成功 ${success} 个`, icon: success ? 'success' : 'none' });
    };
    run();
  },

  convertOne(index, base) {
    const file = this.data.files[index];
    this.setStatus(index, '上传中 0%', 'doing');

    return new Promise((resolve) => {
      const task = wx.uploadFile({
        url: `${base}/api/convert`,
        filePath: file.path,
        name: 'file',
        formData: { target: file.target, filename: file.name },
        timeout: 300000,
        success: (res) => {
          let data = null;
          try {
            data = JSON.parse(res.data);
          } catch (error) {
            data = null;
          }
          if (!data) {
            this.setStatus(index, '失败：响应异常', 'err');
            resolve(false);
            return;
          }
          if (!data.ok) {
            this.setStatus(index, `失败：${data.error}`, 'err');
            resolve(false);
            return;
          }
          const outputs = (data.files || []).map((item) => {
            const ext = extOf(item.name);
            let kind = 'file';
            if (ext === 'pdf' || ext === 'docx') kind = 'doc';
            else if (IMAGE_EXTS.indexOf(ext) >= 0) kind = 'image';
            else if (VIDEO_EXTS.indexOf(ext) >= 0) kind = 'video';
            // 音频、文本、压缩包等无法在小程序内直接预览，改为复制下载链接
            const actionText = kind === 'doc' ? '打开' : (kind === 'image' || kind === 'video' ? '存相册' : '复制链接');
            const url = /^https?:\/\//.test(item.url) ? item.url : `${base}${item.url}`;
            return {
              name: item.name,
              url,
              sizeText: formatSize(item.size),
              ext,
              kind,
              actionText
            };
          });
          this.setData({
            results: this.data.results.concat(outputs),
            log: outputs.length ? `${file.name} → ${outputs[0].name}` : `${file.name} 已处理`
          });
          this.setStatus(index, '完成', 'ok');
          resolve(true);
        },
        fail: (error) => {
          this.setStatus(index, `失败：${error.errMsg || '网络错误'}`, 'err');
          resolve(false);
        }
      });

      if (task && task.onProgressUpdate) {
        task.onProgressUpdate((progress) => {
          this.setStatus(index, `上传中 ${progress.progress}%`, 'doing');
        });
      }
    });
  },

  download(url) {
    return new Promise((resolve, reject) => {
      wx.downloadFile({
        url,
        success: (res) => {
          if (res.statusCode === 200) resolve(res.tempFilePath);
          else reject(new Error(`下载失败 ${res.statusCode}`));
        },
        fail: reject
      });
    });
  },

  openResult(event) {
    const index = event.currentTarget.dataset.index;
    const item = this.data.results[index];
    if (!item) return;

    if (item.kind === 'file' || item.kind === 'audio') {
      wx.setClipboardData({
        data: item.url,
        success: () => wx.showToast({ title: '下载链接已复制', icon: 'success' })
      });
      return;
    }

    wx.showLoading({ title: '下载中', mask: true });
    this.download(item.url).then((filePath) => {
      wx.hideLoading();
      if (item.kind === 'doc') {
        wx.openDocument({
          filePath,
          fileType: item.ext,
          showMenu: true,
          fail: () => wx.showToast({ title: '该格式无法预览，请用电脑下载', icon: 'none' })
        });
      } else if (item.kind === 'image') {
        this.saveAlbum(filePath, 'image');
      } else if (item.kind === 'video') {
        this.saveAlbum(filePath, 'video');
      }
    }).catch(() => {
      wx.hideLoading();
      wx.showToast({ title: '下载失败', icon: 'none' });
    });
  },

  saveAlbum(filePath, type) {
    const save = type === 'video' ? wx.saveVideoToPhotosAlbum : wx.saveImageToPhotosAlbum;
    save({
      filePath,
      success: () => wx.showToast({ title: '已保存到相册', icon: 'success' }),
      fail: (error) => {
        const message = error.errMsg || '';
        if (message.indexOf('auth deny') >= 0 || message.indexOf('authorize') >= 0) {
          wx.showModal({
            title: '需要相册权限',
            content: '请在设置中允许保存到相册后重试。',
            confirmText: '去设置',
            success: (res) => {
              if (res.confirm) wx.openSetting();
            }
          });
        } else {
          wx.showToast({ title: '保存失败，请重试', icon: 'none' });
        }
      }
    });
  }
});
