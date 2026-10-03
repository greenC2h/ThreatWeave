package com.threatweave;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.transaction.annotation.EnableTransactionManagement;

/** ThreatWeave 威胁情报 Java 后端启动入口。 */
@SpringBootApplication
@EnableTransactionManagement
public class ThreatWeaveApplication {

    public static void main(String[] args) {
        SpringApplication.run(ThreatWeaveApplication.class, args);
    }
}
