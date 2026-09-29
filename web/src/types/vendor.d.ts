/**
 * 第三方依赖的最小类型补充。
 *
 * 只给 `d3-hierarchy` 补：它自身不带 .d.ts，仓库里也没有（不能新增依赖，故不装 @types）。
 * 其余依赖（sigma / graphology / @dagrejs/dagre / @xyflow/react）都能自己解析到类型。
 * 这里故意只声明本项目用到的表面（`hierarchy` / `treemap`），避免过度承诺。
 */
declare module "d3-hierarchy" {
  /** 层叠布局里的一个节点：`*0/*1` 是 treemap 算出的矩形（左上右下）。 */
  export interface HierarchyRectangularNode<Datum> {
    readonly data: Datum;
    readonly depth: number;
    readonly height: number;
    readonly parent: HierarchyRectangularNode<Datum> | null;
    readonly children?: HierarchyRectangularNode<Datum>[];
    value?: number;
    x0: number;
    y0: number;
    x1: number;
    y1: number;
    each(callback: (node: HierarchyRectangularNode<Datum>) => void): this;
    eachAfter(callback: (node: HierarchyRectangularNode<Datum>) => void): this;
    eachBefore(callback: (node: HierarchyRectangularNode<Datum>) => void): this;
    descendants(): HierarchyRectangularNode<Datum>[];
    leaves(): HierarchyRectangularNode<Datum>[];
    sum(callback: (datum: Datum) => number): this;
    sort(compare: (a: HierarchyRectangularNode<Datum>, b: HierarchyRectangularNode<Datum>) => number): this;
  }

  export function hierarchy<Datum>(
    data: Datum,
    children?: (datum: Datum) => Datum[] | null | undefined,
  ): HierarchyRectangularNode<Datum>;

  export interface TreemapLayout<Datum> {
    (root: HierarchyRectangularNode<Datum>): HierarchyRectangularNode<Datum>;
    size(size: [number, number]): this;
    tile(tile: unknown): this;
    padding(value: number): this;
    paddingInner(value: number): this;
    paddingOuter(value: number): this;
    round(value: boolean): this;
  }

  export function treemap<Datum>(): TreemapLayout<Datum>;
}
