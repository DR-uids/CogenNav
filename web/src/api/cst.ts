/**
 * CST 查询的共享定义。
 *
 * 树面板与源码面板需要同一份节点数据（树要子节点、源码要高亮范围），
 * 因此把 queryKey/queryFn 集中在这里：两边用同一个 key，谁先请求都不会重复打后端。
 */

import { getCst, type CstResponse } from "./client";

/** 一次下钻的深度：根请求与每次懒展开都用 4 层，展开一次就再往下一屏。 */
export const CST_DEPTH = 4;

export function cstQueryKey(
  repoId: string,
  file: string,
  nodePath: string,
  depth: number = CST_DEPTH,
): readonly [string, string, string, string, number] {
  return ["cst", repoId, file, nodePath, depth];
}

export type CstQueryOptions = {
  queryKey: readonly [string, string, string, string, number];
  queryFn: (context: { signal: AbortSignal }) => Promise<CstResponse>;
  staleTime: number;
  retry: boolean;
};

/**
 * staleTime 设为 Infinity：CST 是一次索引的产物，同一 (文件, 节点, 深度) 不会变，
 * 树与源码面板来回观察同一 key 时也不该触发重复请求。
 */
export function cstQueryOptions(
  repoId: string,
  file: string,
  nodePath: string,
  depth: number = CST_DEPTH,
): CstQueryOptions {
  return {
    queryKey: cstQueryKey(repoId, file, nodePath, depth),
    queryFn: ({ signal }) => getCst(repoId, file, nodePath, depth, signal),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  };
}
