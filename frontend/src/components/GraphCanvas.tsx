import {
  ControlsContainer,
  SigmaContainer,
  ZoomControl,
  useLoadGraph,
  useRegisterEvents,
} from "@react-sigma/core";
import { useEffect, useMemo, useRef } from "react";

import { type Position, toGraphology } from "../graph";
import type { GraphResponse } from "../types";

interface GraphCanvasProps {
  data: GraphResponse;
  selectedTopicId: string | null;
  onSelect: (topicId: string) => void;
}

function GraphController({ data, selectedTopicId, onSelect }: GraphCanvasProps) {
  const loadGraph = useLoadGraph();
  const registerEvents = useRegisterEvents();
  const positions = useRef(new Map<string, Position>());
  const graph = useMemo(
    () => toGraphology(data, selectedTopicId, positions.current),
    [data, selectedTopicId],
  );

  useEffect(() => loadGraph(graph), [graph, loadGraph]);
  useEffect(() => {
    registerEvents({ clickNode: ({ node }) => onSelect(node) });
  }, [onSelect, registerEvents]);
  return null;
}

export function GraphCanvas(props: GraphCanvasProps) {
  return (
    <SigmaContainer
      className="graph-canvas"
      settings={{
        labelDensity: 0.08,
        labelGridCellSize: 120,
        labelRenderedSizeThreshold: 7,
        defaultEdgeColor: "#294a45",
        labelColor: { color: "#dce9e4" },
        zIndex: true,
      }}
    >
      <GraphController {...props} />
      <ControlsContainer position="bottom-right">
        <ZoomControl labels={{ zoomIn: "Przybliż", zoomOut: "Oddal", reset: "Pokaż całość" }} />
      </ControlsContainer>
    </SigmaContainer>
  );
}
