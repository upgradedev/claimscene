import { useMutation, useQuery, type UseQueryResult } from "@tanstack/react-query";
import {
  claimsceneApi,
  type ExtractRequest,
  type ExtractResponse,
  type Health,
  type Manifest,
  type RenderRequest,
  type RenderResponse,
  type Scenario,
} from "./api";

export const queryKeys = {
  health: ["health"] as const,
  scenarios: ["scenarios"] as const,
  manifest: (id: string) => ["manifest", id] as const,
};

export function useHealth(): UseQueryResult<Health> {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => claimsceneApi.health(),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useScenarios(): UseQueryResult<Scenario[]> {
  return useQuery({
    queryKey: queryKeys.scenarios,
    queryFn: () => claimsceneApi.scenarios(),
    staleTime: 5 * 60_000,
  });
}

export function useManifest(caseId: string | null): UseQueryResult<Manifest> {
  return useQuery({
    queryKey: queryKeys.manifest(caseId ?? ""),
    queryFn: () => claimsceneApi.manifest(caseId as string),
    enabled: !!caseId,
    staleTime: Infinity,
  });
}

export function useExtract() {
  return useMutation<ExtractResponse, Error, ExtractRequest>({
    mutationFn: (req) => claimsceneApi.extract(req),
  });
}

export function useRender() {
  return useMutation<RenderResponse, Error, RenderRequest>({
    mutationFn: (req) => claimsceneApi.render(req),
  });
}
