import { useState, type ReactNode } from "react";
import { createMaterialListMemory, MaterialListMemoryContext } from "./materialListMemory";

export function MaterialListMemoryProvider({ children }: { children: ReactNode }) {
  const [memory] = useState(createMaterialListMemory);
  return <MaterialListMemoryContext.Provider value={memory}>{children}</MaterialListMemoryContext.Provider>;
}
