from dynatrace_extension import Extension, Status, StatusValue
import requests, traceback 
from datetime import datetime, timezone, timedelta

class ExtensionImpl(Extension):

    def initialize(self):
        """
        Runs on extension startup 
        """
        self.logger.info("Initializing azure_ddu_monitoring.")

        for endpoint in self.activation_config["endpoints"]:

            # Dynatrace Tenant URL | Managed: https://{your-domain}/e/{your-environment-id} | SaaS: https://{your-environment-id}.live.dynatrace.com
            environment_url = endpoint["environment_url"]

            # API Token with following permissions: Read entities, Read metrics
            api_token = endpoint["api_token"]

            # Query interval in minutes to collect DDU consumption (Minimum: 15 min)
            query_interval_min = endpoint["query_interval_min"]

            # Reporting mode - if enabled summarize consumption by Azure subscription otherwise by Azure entity
            summarize_by_subscription = endpoint["summarize_by_subscription"]

            # Enable/disable verify SSL certificate for API requests
            verify_ssl = endpoint["verify_ssl"]

            # ================================================================================================
            # ================================================================================================

            # Schedule main function according to query interval
            self.schedule(
                self.report_azure_consumption, 
                query_interval_min*60, 
                args=(environment_url, api_token, query_interval_min, summarize_by_subscription, verify_ssl)
                )

            self.logger.info(f"Scheduled query for endpoint with url '{environment_url}' and {query_interval_min} min interval")

            # ================================================================================================
            # ================================================================================================

    def query(self):
        """
        The query method is automatically scheduled to run every minute
        """

    def fastcheck(self) -> Status:
        """
        This is called when the extension runs for the first time.
        If this AG cannot run this extension, raise an Exception or return StatusValue.ERROR!
        """
        return Status(StatusValue.OK)
    
    def report_azure_consumption(self, environment_url, api_token, query_interval_min, summarize_by_subscription, verify_ssl):

        # ================================================================================================
        # ================================================================================================

        # Collect and report metric DDU consumption for Azure services

        # ================================================================================================
        # ================================================================================================

        try:
            self.logger.info("Query method started for azure_ddu_monitoring.")

            MONITORED_ENTITIES_API = environment_url + "/api/v2/entities"
            METRICS_QUERY_API = environment_url + "/api/v2/metrics/query"

            datetime_to = datetime.now(timezone.utc)
            datetime_from = datetime_to - timedelta(minutes=query_interval_min)

            TIME_TO = datetime_to.isoformat(timespec='milliseconds')
            TIME_FROM = datetime_from.isoformat(timespec='milliseconds')

            # Class for mapping DDU consumption to Azure entities and their Azure subscriptions
            class ConsumptionRecord:

                # Constructor function    
                def __init__(self, 
                            entity_id = "Undefined", 
                            entity_name = "Undefined", 
                            entity_type = "Undefined", 
                            subscription_id = "Undefined", 
                            subscription_name = "Undefined", 
                            metric_ddus = 0):
                    
                    self.entity_id = entity_id
                    self.entity_name = entity_name
                    self.entity_type = entity_type
                    self.subscription_id = subscription_id
                    self.subscription_name = subscription_name
                    self.metric_ddus = metric_ddus

            # List of Azure Classic entity types relevant for consumption reporting
            CLASSIC_TYPES = [
                "AZURE_API_MANAGEMENT_SERVICE",
                "AZURE_REDIS_CACHE",
                "AZURE_VM",
                "AZURE_VM_SCALE_SET",
                "AZURE_IOT_HUB",
                "AZURE_COSMOS_DB",
                "AZURE_EVENT_HUB_NAMESPACE",
                "AZURE_EVENT_HUB",
                "AZURE_APPLICATION_GATEWAY",
                "AZURE_LOAD_BALANCER",
                "AZURE_SERVICE_BUS_NAMESPACE",
                "AZURE_SERVICE_BUS_TOPIC",
                "AZURE_SERVICE_BUS_QUEUE",
                "AZURE_SQL_SERVER",
                "AZURE_SQL_DATABASE",
                "AZURE_SQL_ELASTIC_POOL",
                "AZURE_STORAGE_ACCOUNT"
            ]

            # Dictionary for consumption data by entity ID
            entity_dict = {}

            # Dictionary for Azure Subscriptions by entity ID
            subscription_entity_dict = {}


            # Fetch all Azure Subscription entities
            # ================================================================================================

            params = { 
                "api-token": api_token, 
                "pageSize": 500,
                "entitySelector": "type(AZURE_SUBSCRIPTION)",
                "fields": "+properties",
                "from": "now-24h", 
                "to": TIME_TO
            }

            next_page_key = ""
            while(True):
                if next_page_key:
                    params = { 
                        "api-token": api_token, 
                        "nextPageKey": next_page_key 
                    }
                response = requests.get(MONITORED_ENTITIES_API, params, verify=verify_ssl)
                json = response.json()

                subscription_entities = json["entities"]
                for subscription_entity in subscription_entities:
                    subscription_entity_dict[subscription_entity["entityId"]] = {
                        "subscription_id": subscription_entity["properties"]["azureSubscriptionUuid"],
                        "subscription_name": subscription_entity["displayName"]
                    }

                next_page_key = json.get("nextPageKey")
                if not next_page_key:
                    break

            self.logger.info(f"Fetched {len(subscription_entity_dict)} Azure Subscriptions.")


            # Collect DDU metric consumption for each Azure subscription
            # ================================================================================================

            for subscription_entity_id in subscription_entity_dict:
            
                subscription_id = subscription_entity_dict[subscription_entity_id]["subscription_id"]
                subscription_name = subscription_entity_dict[subscription_entity_id]["subscription_name"]

                self.logger.info(f"Collecting consumption of Cloud Azure entities for subscription {subscription_id} / {subscription_name}.")

                # Fetch billed DDUs of Cloud Azure entities for the given Azure subscription
                # ================================================================================================
                metric_consumption_list = []
                metric_selector = f"builtin:billing.ddu.metrics.byEntity:filter(in(\"dt.entity.monitored_entity\", entitySelector(\"type(~\"CUSTOM_DEVICE~\"),fromRelationship.belongsTo(type(~\"AZURE_SUBSCRIPTION~\"),azureSubscriptionUuid({subscription_id}))\"))):splitBy(\"dt.entity.monitored_entity\"):fold(sum)"
                params = {
                    "api-token": api_token, 
                    "metricSelector": metric_selector,
                    "from": TIME_FROM,
                    "to": TIME_TO,
                    "pageSize": 10000
                }
                
                next_page_key = ""
                while(True):
                    if next_page_key:
                        params = { 
                            "api-token": api_token, 
                            "nextPageKey": next_page_key 
                        }
                    response = requests.get(METRICS_QUERY_API, params, verify=verify_ssl)
                    json = response.json()
                    
                    metric_consumption_list.extend(json["result"][0]["data"])
                    
                    next_page_key = json.get("nextPageKey")
                    if not next_page_key:
                        break

                self.logger.info(f"Fetched consumption for {len(metric_consumption_list)} entities of subscription {subscription_id}.")


                # Fetch Cloud Azure entities for the given Azure subscription
                # ================================================================================================
                azure_entities = []
                entity_selector = f"type(CUSTOM_DEVICE),fromRelationships.belongsTo(type(AZURE_SUBSCRIPTION),azureSubscriptionUuid({subscription_id}))"
                params = { 
                    "api-token": api_token, 
                    "pageSize": 500,
                    "entitySelector": entity_selector,
                    "from": "now-24h", 
                    "to": TIME_TO
                }

                next_page_key = ""
                while(True):
                    if next_page_key:
                        params = { 
                            "api-token": api_token, 
                            "nextPageKey": next_page_key 
                        }
                    response = requests.get(MONITORED_ENTITIES_API, params, verify=verify_ssl)
                    json = response.json()

                    azure_entities.extend(json["entities"])

                    next_page_key = json.get("nextPageKey")
                    if not next_page_key:
                        break

                self.logger.info(f"Fetched {len(azure_entities)} Cloud Azure entities of subscription {subscription_id}.")

                # Create consumption records per Azure entity
                # ================================================================================================

                for metric_consumption in metric_consumption_list:
                
                    entity_id = metric_consumption["dimensionMap"]["dt.entity.monitored_entity"]
                    metric_ddus = metric_consumption["values"][0]

                    entity = next((azure_entity for azure_entity in azure_entities if azure_entity["entityId"] == entity_id), None)
                    
                    if (entity is not None):
                        entity_name = entity["displayName"]
                        entity_type = entity["type"]

                    entity_dict[entity_id] = ConsumptionRecord(entity_id, entity_name, entity_type, subscription_id, subscription_name, metric_ddus)


            # Collect DDU metric consumption for each Classic Azure entity type
            # ================================================================================================

            self.logger.info(f"Collecting consumption of Classic Azure entities for all subscriptions.")

            for classic_type in CLASSIC_TYPES:
                
                # Fetch billed DDUs for given Classic type
                # ================================================================================================
                classic_metric_consumption_list = []
                metric_selector = f"builtin:billing.ddu.metrics.byEntity:filter(prefix(\"dt.entity.monitored_entity\", {classic_type})):splitBy(\"dt.entity.monitored_entity\"):fold(sum):names"
                params = {
                    "api-token": api_token, 
                    "metricSelector": metric_selector,
                    "from": TIME_FROM,
                    "to": TIME_TO,
                    "pageSize": 10000
                }
                
                next_page_key = ""
                while(True):
                    if next_page_key:
                        params = { 
                            "api-token": api_token, 
                            "nextPageKey": next_page_key 
                        }
                    response = requests.get(METRICS_QUERY_API, params, verify=verify_ssl)
                    json = response.json()
                    
                    classic_metric_consumption_list.extend(json["result"][0]["data"])
                    
                    next_page_key = json.get("nextPageKey")
                    if not next_page_key:
                        break 
                
                self.logger.info(f"Fetched consumption for {len(classic_metric_consumption_list)} Azure entities of Classic type {classic_type}.")

                # Fetch entities for given Classic type
                # ================================================================================================

                if len(classic_metric_consumption_list) > 0:
                    
                    classic_entities = []
                    params = { 
                        "api-token": api_token, 
                        "pageSize": 500,
                        "entitySelector": f"type({classic_type})",
                        "fields": "+fromRelationships.isAccessibleBy",
                        "from": "now-24h", 
                        "to": TIME_TO
                    }
                    next_page_key = ""
                    while(True):
                        if next_page_key:
                            params = { 
                                "api-token": api_token, 
                                "nextPageKey": next_page_key 
                            }
                        response = requests.get(MONITORED_ENTITIES_API, params, verify=verify_ssl)
                        json = response.json()

                        classic_entities.extend(json["entities"])

                        next_page_key = json.get("nextPageKey")
                        if not next_page_key:
                            break

                    self.logger.info(f"Fetched {len(classic_metric_consumption_list)} entities of Classic type {classic_type}.")

                    # Create consumption records per Azure entity for given Classic type
                    # ================================================================================================
                    for classic_metric_consumption in classic_metric_consumption_list:

                        entity_id = classic_metric_consumption["dimensionMap"]["dt.entity.monitored_entity"]
                        entity_name = classic_metric_consumption["dimensionMap"]["dt.entity.monitored_entity.name"]
                        entity_type = entity_id.split("-")[0] # e.g. AZURE_WEB_APP-F0991A85FA6C2703
                        metric_ddus = classic_metric_consumption["values"][0]

                        entity = next((classic_entity for classic_entity in classic_entities if classic_entity["entityId"] == entity_id), None)

                        if (entity is not None):
                            subscription_entity = next((accessible_by for accessible_by in entity["fromRelationships"]["isAccessibleBy"] if accessible_by["type"] == "AZURE_SUBSCRIPTION"), None)

                            if (subscription_entity is not None):
                                subscription_entity_id = subscription_entity["id"]
                                subscription_id = subscription_entity_dict[subscription_entity_id]["subscription_id"]
                                subscription_name = subscription_entity_dict[subscription_entity_id]["subscription_name"]
                        
                        entity_dict[entity_id] = ConsumptionRecord(entity_id, entity_name, entity_type, subscription_id, subscription_name, metric_ddus)


            self.logger.info(f"Finished collection for total {len(entity_dict)} entities.")

            # Report consumption either by Azure subscription or Azure entity
            # ================================================================================================
            metric_lines = []

            if summarize_by_subscription:

                # Report consumption by Azure subscription
                # ================================================================================================
                subscription_dict = {}
                
                # Summarize consumption by subscription
                for record in entity_dict.values():
                    if record.subscription_id in subscription_dict:
                        subscription_dict[record.subscription_id][metric_ddus] += record.metric_ddus
                    else:
                        subscription_dict[record.subscription_id] = {
                            "subscription_id": record.subscription_id,
                            "subscription_name": record.subscription_name,
                            "metric_ddus": record.metric_ddus
                        }
                
                for subscription in subscription_dict.values():
                    dimensions = f"azure.subscription.id={subscription['subscription_id']},azure.subscription.name={subscription['subscription_name']},environment.url={environment_url}"
                    metric_line = f"consumption.ddu.metrics.azure.ddus_by_subscription,{dimensions} {subscription['metric_ddus']}"
                    metric_lines.append(metric_line)
            
            else:
                # Report consumption by Azure entity
                # ================================================================================================
                for record in entity_dict.values():
                    dimensions = f"dt.entity.custom_device={record.entity_id},entity.name={record.entity_name},entity.type={record.entity_type},azure.subscription.id={record.subscription_id},azure.subscription.name={record.subscription_name},environment.url={environment_url}"
                    
                    metric_line = f"consumption.ddu.metrics.azure.ddus_by_entity,{dimensions} {record.metric_ddus}"
                    metric_lines.append(metric_line)
            
            # Report metrics according to metric line protocol in batches
            # ================================================================================================
            if metric_lines:
                batch_size = 500
                batches = [metric_lines[i:i + batch_size] for i in range(0, len(metric_lines), batch_size)]

                for batch in batches:
                    self.report_mint_lines(batch)

                self.logger.info(f"Successfully reported {len(metric_lines)} consumption metrics.")

            self.logger.info("Query method ended for azure_ddu_monitoring.")

        except:
            self.logger.error("ERROR WHILE REPORTING AZURE CONSUMPTION")
            self.logger.error(traceback.format_exc())

def main():
    ExtensionImpl(name="azure_ddu_monitoring").run()



if __name__ == '__main__':
    main()
