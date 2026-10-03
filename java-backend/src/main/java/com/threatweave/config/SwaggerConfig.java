package com.threatweave.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Contact;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.info.License;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Swagger/OpenAPI 配置类
 */
@Configuration
public class SwaggerConfig {

    @Bean
    public OpenAPI openAPI() {
        return new OpenAPI()
                .info(new Info()
                        .title("ThreatWeave API")
                        .description("ThreatWeave 威胁情报 REST API 文档")
                        .version("1.0.0")
                        .contact(new Contact()
                                .name("ThreatWeave")
                                .email("support@threatweave.local")
                                .url("http://127.0.0.1:18080/swagger-ui/index.html"))
                        .license(new License()
                                .name("Apache 2.0")
                                .url("https://www.apache.org/licenses/LICENSE-2.0.html")));
    }
}
