import * as cdk from "aws-cdk-lib"
import * as location from "aws-cdk-lib/aws-location"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import { resourceName } from "./naming"

export interface GeoConstructProps {
  config: AppConfig
}

/**
 * Amazon Location geo resources for the crew router (design §16.1).
 *
 * The provisioned route calculator resource used for the demo. The tool's flood avoidance and
 * the mandatory post-route re-test are computed in pure shapely code (ADR-2), so route quality
 * never affects the safety guarantee — only the re-test does (A6).
 *
 * The geofence collection and its crew-entry mirroring path are deferred (R3.7, task 70.1); the
 * Flood_Store is the only hazard source in the challenge tier (ADR-2).
 */
export class GeoConstruct extends Construct {
  public readonly routeCalculator: location.CfnRouteCalculator

  constructor(scope: Construct, id: string, props: GeoConstructProps) {
    super(scope, id)

    const { config } = props

    this.routeCalculator = new location.CfnRouteCalculator(this, "RouteCalculator", {
      calculatorName: resourceName(config, "routes"),
      dataSource: "Here",
      description: "grid-tools crew route calculator (flood avoidance re-tested in-tool, §16.1)",
      tags: [{ key: "project", value: "minnal" }],
    })
    // Retain in prod-like envs; dev is disposable (steering `infra-cdk.md`).
    this.routeCalculator.applyRemovalPolicy(
      config.grid_tools.env === "dev" ? cdk.RemovalPolicy.DESTROY : cdk.RemovalPolicy.RETAIN
    )
  }
}
