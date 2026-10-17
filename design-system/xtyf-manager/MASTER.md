# 巡天御风管理系统 — NASA Punk 设计系统

**Style:** Retro-Futurism (NASA Punk / Cyberpunk / Synthwave)  
**Product Type:** Dashboard / Technical Management Platform  
**Generated:** 2024-12-18  
**Preferred Mode:** Dark

---

## 🎨 核心配色方案

### Primary Palette (NASA Punk Neons)
```css
--neon-cyan:    #00FFFF;  /* 主强调色 — CTA、活跃状态 */
--neon-blue:    #0080FF;  /* 次强调色 — 链接、信息 */
--hot-pink:     #FF006E;  /* 警告/紧急 */
--purple:       #5D34D0;  /* 辅助强调 */
--gold:         #FFD700;  /* 成功/完成 */
--deep-black:   #0A0A14;  /* 背景底色 */
--panel-black:  #1A1A2E;  /* 面板/卡片背景 */
--grid-cyan:    #00FFFF33; /* 网格线（20% 透明度）*/
```

### Functional Colors
```css
--success:      #00FF88;  /* 成功状态 — 带霓虹绿调 */
--warning:      #FF9500;  /* 警告 — 橙色霓虹 */
--danger:       #FF006E;  /* 危险/错误 — hot pink */
--info:         #0080FF;  /* 信息提示 */
```

### Text & UI Elements
```css
--text-primary:   #FFFFFF;
--text-secondary: #A0A0C0;  /* 带紫调的灰 */
--text-muted:     #606080;
--border-glow:    #00FFFF;  /* 发光边框 */
--scanline:       rgba(0, 255, 255, 0.03); /* CRT 扫描线 */
```

---

## 📐 间距与布局

### Spacing Scale (NASA Grid — 基于 8px，但密集化仪表板布局)
```css
--sp-1: 4px;   /* 紧密元素 */
--sp-2: 8px;   /* 小间距 */
--sp-3: 12px;  /* 标准内边距 */
--sp-4: 16px;  /* 卡片内边距 */
--sp-5: 24px;  /* 区块间距 */
--sp-6: 32px;  /* 大区块 */
--sp-8: 48px;  /* 页面边距 */
```

### Grid & Columns
- **侧栏：** 220px（收起后 60px，只显示图标）
- **主内容：** `minmax(600px, 1fr)`
- **卡片间隙：** 16px
- **响应断点：** 1280px / 768px / 480px

---

## 🔤 字体系统

### Primary Typeface: **IBM Plex Mono** (等宽，NASA 控制台风格)
- **Body:** 14px / 1.6 / 400
- **Heading 1:** 28px / 1.2 / 700
- **Heading 2:** 22px / 1.3 / 600
- **Heading 3:** 18px / 1.4 / 600
- **Code/Data:** 13px / 1.5 / 400 (单色显示器风格)

**Fallback Stack:**
```css
font-family: 'IBM Plex Mono', 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
```

### Display Font (可选 — 用于 logo 或重大标题): **Orbitron** (几何未来感)
```css
font-family: 'Orbitron', 'IBM Plex Mono', sans-serif;
font-weight: 700;
letter-spacing: 0.05em;
text-transform: uppercase;
```

---

## 🎭 特效与动画

### Neon Glow (霓虹发光效果)
```css
.neon-text {
  color: var(--neon-cyan);
  text-shadow: 
    0 0 5px currentColor,
    0 0 10px currentColor,
    0 0 20px currentColor,
    0 0 40px currentColor;
}

.neon-border {
  border: 1px solid var(--neon-cyan);
  box-shadow: 
    0 0 5px var(--neon-cyan),
    inset 0 0 5px var(--neon-cyan);
}
```

### CRT Scanlines (复古 CRT 扫描线)
```css
body::before {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 9999;
  background: repeating-linear-gradient(
    0deg,
    rgba(0, 0, 0, 0.15),
    rgba(0, 0, 0, 0.15) 1px,
    transparent 1px,
    transparent 2px
  );
  animation: scanline 8s linear infinite;
}

@keyframes scanline {
  0% { transform: translateY(0); }
  100% { transform: translateY(100%); }
}
```

### Glitch Effect (故障艺术 — 用于关键时刻)
```css
@keyframes glitch {
  0%, 100% { transform: translate(0); }
  20% { transform: translate(-2px, 2px); }
  40% { transform: translate(-2px, -2px); }
  60% { transform: translate(2px, 2px); }
  80% { transform: translate(2px, -2px); }
}

.glitch-trigger:hover {
  animation: glitch 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94);
}
```

### Motion Timing
- **Micro:** 150ms `cubic-bezier(0.4, 0.0, 0.2, 1)` — 按钮 hover
- **Standard:** 250ms `ease-out` — 面板展开
- **Dramatic:** 400ms `cubic-bezier(0.68, -0.55, 0.265, 1.55)` — modal 弹出

---

## 🧩 组件设计规则

### 按钮
```css
.btn-primary {
  background: transparent;
  border: 2px solid var(--neon-cyan);
  color: var(--neon-cyan);
  padding: 10px 24px;
  font-family: 'IBM Plex Mono', monospace;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  box-shadow: 0 0 10px var(--neon-cyan);
  transition: all 250ms ease-out;
}

.btn-primary:hover {
  background: var(--neon-cyan);
  color: var(--deep-black);
  box-shadow: 
    0 0 20px var(--neon-cyan),
    0 0 40px var(--neon-cyan),
    inset 0 0 10px var(--neon-cyan);
  transform: translateY(-2px);
}
```

### 卡片/面板
```css
.card {
  background: var(--panel-black);
  border: 1px solid rgba(0, 255, 255, 0.3);
  border-radius: 4px; /* 方正几何，NASA 控制台风格 */
  padding: var(--sp-4);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.6);
  position: relative;
}

.card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 2px;
  background: linear-gradient(90deg, var(--neon-cyan), transparent);
}
```

### 输入框
```css
.input {
  background: rgba(0, 255, 255, 0.05);
  border: 1px solid var(--neon-cyan);
  border-radius: 2px;
  padding: 10px 14px;
  color: var(--text-primary);
  font-family: 'IBM Plex Mono', monospace;
  font-size: 14px;
  box-shadow: inset 0 0 8px rgba(0, 255, 255, 0.2);
}

.input:focus {
  outline: none;
  border-color: var(--neon-cyan);
  box-shadow: 
    0 0 8px var(--neon-cyan),
    inset 0 0 12px rgba(0, 255, 255, 0.3);
}
```

### 进度条（带霓虹流动效果）
```css
.progress-track {
  height: 8px;
  background: rgba(0, 255, 255, 0.1);
  border: 1px solid rgba(0, 255, 255, 0.3);
  border-radius: 0;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--neon-cyan), var(--neon-blue));
  box-shadow: 0 0 10px var(--neon-cyan);
  animation: neon-pulse 2s ease-in-out infinite;
}

@keyframes neon-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}
```

### 徽章（紧急度/状态）
```css
.badge-high {
  background: rgba(255, 0, 110, 0.2);
  border: 1px solid var(--hot-pink);
  color: var(--hot-pink);
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  box-shadow: 0 0 8px var(--hot-pink);
}
```

---

## 🖼️ 背景网格（NASA 控制室风格）

```css
body {
  background-color: var(--deep-black);
  background-image: 
    linear-gradient(var(--grid-cyan) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-cyan) 1px, transparent 1px);
  background-size: 40px 40px;
  background-position: -1px -1px;
}
```

---

## ⚠️ Anti-Patterns（避免事项）

1. **NO 圆角过大** — NASA punk 是方正几何风格，`border-radius` 不超过 4px
2. **NO 柔和渐变** — 只用硬边界 neon glow，不用 soft blur
3. **NO 亮色背景** — 必须深色（`--deep-black` / `--panel-black`）
4. **NO 非等宽字体用于数据** — 数据表格/代码块必须 monospace
5. **NO 过度动画** — glitch 效果节制使用（仅关键时刻），避免全屏闪烁
6. **NO 低对比度** — 霓虹色 + 黑色背景对比度天然高，但要确保文字至少 7:1

---

## ✅ 实现检查清单

- [ ] 全局字体设置为 `IBM Plex Mono`
- [ ] 深色背景 + 霓虹网格已应用
- [ ] 主色调 `--neon-cyan` 用于 CTA 和活跃状态
- [ ] 按钮带霓虹发光 hover 效果
- [ ] CRT 扫描线动画已添加（可选降低不透明度）
- [ ] 卡片顶部带霓虹渐变线
- [ ] 进度条带霓虹脉冲动画
- [ ] 输入框 focus 状态有发光效果
- [ ] 紧急度徽章使用 `--hot-pink` / `--warning` / `--success`
- [ ] 方正几何布局（`border-radius ≤ 4px`）
- [ ] 对比度验证通过（WCAG AAA，7:1+）
- [ ] `prefers-reduced-motion` 已处理（禁用 scanline/glitch）

---

## 🔗 参考资源

- **Style Origin:** Retro-Futurism (#11) — 1980s NASA mission control aesthetics
- **Color Theory:** Neon complementary pairs (Cyan-Pink, Blue-Gold)
- **Typography:** IBM Plex Mono (monospace, open-source), Orbitron (geometric display)
- **Animation Guidance:** GSAP presets (if needed) — `scroll-reveal`, `stagger-fade`

---

**Last Updated:** 2024-12-18  
**Approved By:** [待用户确认]
