/**
 * 测试环境准备。
 *
 * 只做两件事：接上 jest-dom 的断言，以及把每个用例之后的 DOM 清干净。
 * 刻意不引入任何全局的 fetch mock —— 「哪个用例需要什么数据」应该在用例里显式写出来，
 * 藏在 setup 里的全局桩会让读测试的人无法判断它到底依赖什么。
 */
// jest-dom 的 /vitest 入口同时完成两件事：注册断言 + 把 toBeInTheDocument 这类匹配器的
// 类型 augment 到 vitest 的 expect 上。用 /vitest 而不是 /matchers，否则类型检查会红
// （运行时能用、tsc 报未知匹配器），是一处「本地看着没事、CI 才发现」的坑。
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// 注：vitest 5.0.1 在 setup 阶段（suite 上下文建立前）执行本文件顶层代码，
// 此处顶层 afterEach 会触发 "failed to find current suite" 并破坏后续所有测试文件。
// 因此 DOM 清理改由每个测试文件在自己的 describe 内 afterEach 完成（见各 *.test.*）。
void afterEach
void cleanup
