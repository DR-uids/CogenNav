import { helper } from "./helper";

export interface Options {
  name: string;
}

export class Runner {
  run(): string {
    return helper(this.name);
  }

  name = "runner";
}

export function main(): string {
  const runner = new Runner();
  return runner.run();
}
