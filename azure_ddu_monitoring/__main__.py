from dynatrace_extension import Extension, Status, StatusValue
import requests
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

            # Query interval in minutes to perform DDU consumption collection
            query_interval_min = endpoint["query_interval_min"]

            # Reporting mode - if enabled summarize consumption by Azure subscription otherwise by Azure entity
            summarize_by_subscription = endpoint["summarize_by_subscription"]

            # ================================================================================================
            # ================================================================================================

            # Schedule main function according to query interval
            self.schedule(
                self.report_azure_consumption, 
                query_interval_min*60, 
                args=(environment_url, api_token, query_interval_min, summarize_by_subscription)
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
    
    def report_azure_consumption(self, environment_url, api_token, query_interval_min, summarize_by_subscription):

        # ================================================================================================
        # ================================================================================================

        # Collect and report metric DDU consumption for Azure services

        # ================================================================================================
        # ================================================================================================

        self.logger.info("Query method started for azure_ddu_monitoring.")

        datetime_to = datetime.now(timezone.utc)
        datetime_from = datetime_to - timedelta(minutes=query_interval_min)

        time_to = datetime_to.isoformat(timespec='milliseconds')
        time_from = datetime_from.isoformat(timespec='milliseconds')

        # Class for mapping DDU consumption to Azure entities and their Azure subscriptions
        class ConsumptionRecord:

            # constructor function    
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

        # Fetch billed DDUs for all Azure entities with an Azure Subscription
        # ================================================================================================
        metric_consumption_list = []
        metrics_query_api = environment_url + "/api/v2/metrics/query"
        metric_selector = "builtin:billing.ddu.metrics.byEntity:filter(in(\"dt.entity.monitored_entity\", entitySelector(\"type(~\"CUSTOM_DEVICE~\"),fromRelationship.belongsTo(type(~\"AZURE_SUBSCRIPTION~\"))\"))):splitBy(\"dt.entity.monitored_entity\"):fold(sum)"
        params = {
            "api-token": api_token, 
            "metricSelector": metric_selector,
            "from": time_from,
            "to": time_to,
            "pageSize": 10000
        }
        
        next_page_key = ""
        while(True):
            if next_page_key:
                params = { 
                    "api-token": api_token, 
                    "nextPageKey": next_page_key 
                }
            response = requests.get(metrics_query_api, params)
            json = response.json()
            
            metric_consumption_list.extend(json["result"][0]["data"])
            
            next_page_key = json.get("nextPageKey")
            if not next_page_key:
                break

        # Fetch Azure entities including their Azure Subscription
        # ================================================================================================
        azure_entities = []
        monitored_entities_api = environment_url + "/api/v2/entities"
        entity_selector = "type(CUSTOM_DEVICE),fromRelationships.belongsTo(type(AZURE_SUBSCRIPTION))"
        params = { 
            "api-token": api_token, 
            "pageSize": 500,
            "entitySelector": entity_selector,
            "fields": "+properties.CUSTOMPROPERTIES",
            "from": "now-24h", 
            "to": time_to,
            "nextPageKey": next_page_key
        }

        next_page_key = ""
        while(True):
            if next_page_key:
                params = { 
                    "api-token": api_token, 
                    "nextPageKey": next_page_key 
                }
            response = requests.get(monitored_entities_api, params)
            json = response.json()

            azure_entities.extend(json["entities"])

            next_page_key = json.get("nextPageKey")
            if not next_page_key:
                break

        # Create dictionary with DDU consumption by Azure entity including Azure subscription
        # ================================================================================================
        entity_dict = {}

        for metric_consumption in metric_consumption_list:
            
            entity_id = metric_consumption["dimensionMap"]["dt.entity.monitored_entity"]
            metric_ddus = metric_consumption["values"][0]

            entity = next((azure_entity for azure_entity in azure_entities if azure_entity["entityId"] == entity_id), None)
            
            if (entity is not None):
                entity_name = entity["displayName"]
                entity_type = entity["type"]
                properties = entity["properties"]

                if "customProperties" in properties:
                    custom_properties = properties["customProperties"]
                    subscription = next((prop for prop in custom_properties if prop["key"] == "Subscription"), None)

                    if (subscription is not None):
                        subscription_id_and_name = subscription["value"] # value pattern: "<subscription-id> <subscription-name>"
                        split = subscription_id_and_name.split(" ")
                        subscription_id = split[0]
                        subscription_name = split[1]

            entity_dict[entity_id] = ConsumptionRecord(entity_id, entity_name, entity_type, subscription_id, subscription_name, metric_ddus)

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
                dimensions = f"azure.subscription.id={subscription['subscription_id']},azure.subscription.name={subscription['subscription_name']}"
                metric_line = f"consumption.ddu.metrics.azure.ddus_by_subscription,{dimensions} {subscription['metric_ddus']}"
                metric_lines.append(metric_line)
        
        else:
            # Report consumption by Azure entity
            # ================================================================================================
            for record in entity_dict.values():
                dimensions = f"dt.entity.custom_device={record['entity_id']},entity.name={record['entity_name']},entity.type={record['entity_type']},azure.subscription.id={record['subscription_id']},azure.subscription.name={record['subscription_name']}"
                
                metric_line = f"consumption.ddu.metrics.azure.ddus_by_entity,{dimensions} {record['metric_ddus']}"
                metric_lines.append(metric_line)
        
        # Report metrics according to metric line protocol in batches
        # ================================================================================================
        if metric_lines:
            batch_size = 500
            batches = [metric_lines[i:i + batch_size] for i in range(0, len(metric_lines), batch_size)]

            for batch in batches:
                self.report_mint_lines(batch)

            self.logger.info("Successfully reported consumption metrics.")

        self.logger.info("Query method ended for azure_ddu_monitoring.")

def main():
    ExtensionImpl(name="azure_ddu_monitoring").run()



if __name__ == '__main__':
    main()