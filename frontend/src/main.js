import { createApp } from 'vue'
import naive from 'naive-ui'
import App from './App.vue'
import router from './router'

// 全局注册 Naive UI。
// 各页面用了大量 n-* 组件，逐个 import 很啰嗦；未注册的组件会被 Vue 当成
// 未知标签，子内容直接内联输出（表现为"弹窗内容摊在页面上、表格空白"），
// 而且控制台不会报错——这个坑很难从表面看出来。
createApp(App).use(router).use(naive).mount('#app')
